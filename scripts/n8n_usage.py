#!/usr/bin/env python3
"""Ranking de uso de los workflows de n8n, para priorizar la migración.

Pagina la API de n8n, agrega ejecuciones por workflow (total, errores, última
ejecución) y marca cuáles ya están migrados a la plataforma comparando contra
el campo `replaces` de cada FlowSpec del registro.

Uso:
    N8N_BASE_URL=https://tu-n8n.example.com N8N_API_KEY=... \
        uv run python scripts/n8n_usage.py [--days 30] [--max-pages 40]

La API key se crea en n8n: Settings → n8n API → Create API key.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _get(client: httpx.Client, base: str, path: str, **params) -> dict:
    r = client.get(f"{base}/api/v1{path}", params={k: v for k, v in params.items() if v})
    r.raise_for_status()
    return r.json()


def fetch_workflows(client: httpx.Client, base: str) -> dict[str, dict]:
    """id → {name, active}, paginando."""
    out: dict[str, dict] = {}
    cursor = None
    while True:
        page = _get(client, base, "/workflows", limit=100, cursor=cursor)
        for w in page.get("data", []):
            out[str(w["id"])] = {"name": w.get("name", "?"), "active": w.get("active", False)}
        cursor = page.get("nextCursor")
        if not cursor:
            return out


def fetch_executions(
    client: httpx.Client, base: str, since: datetime, max_pages: int
) -> list[dict]:
    """Ejecuciones desde `since`, más recientes primero; corta al pasar la fecha."""
    rows: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        page = _get(client, base, "/executions", limit=250, cursor=cursor)
        data = page.get("data", [])
        for e in data:
            started = e.get("startedAt") or e.get("createdAt") or ""
            if started and datetime.fromisoformat(started.replace("Z", "+00:00")) < since:
                return rows
            rows.append(e)
        cursor = page.get("nextCursor")
        if not cursor or not data:
            return rows
    print("aviso: corte en --max-pages; puede faltar historial anterior", file=sys.stderr)
    return rows


def migrated_names() -> dict[str, str]:
    """Nombre de workflow n8n (normalizado) → slug de la plataforma, vía `replaces`."""
    from app.flows.registry import discover

    out: dict[str, str] = {}
    for slug, spec in discover().items():
        for piece in spec.replaces.replace("(+", "(").split("(+"):
            name = piece.strip(" ()+")
            if name:
                out[name.lower()] = slug
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="ventana de análisis (default 30)")
    parser.add_argument("--max-pages", type=int, default=40, help="tope de páginas de 250")
    args = parser.parse_args()

    base = os.environ.get("N8N_BASE_URL", "").rstrip("/")
    key = os.environ.get("N8N_API_KEY", "")
    if not base or not key:
        _die("define N8N_BASE_URL y N8N_API_KEY (Settings → n8n API en tu n8n)")

    since = datetime.now(UTC) - timedelta(days=args.days)
    with httpx.Client(headers={"X-N8N-API-KEY": key}, timeout=30) as client:
        workflows = fetch_workflows(client, base)
        executions = fetch_executions(client, base, since, args.max_pages)

    stats: dict[str, dict] = defaultdict(lambda: {"total": 0, "errors": 0, "last": ""})
    for e in executions:
        wid = str(e.get("workflowId", "?"))
        s = stats[wid]
        s["total"] += 1
        if e.get("status") not in ("success", "waiting"):
            s["errors"] += 1
        started = e.get("startedAt") or ""
        if started > s["last"]:
            s["last"] = started

    migrated = migrated_names()
    total = len(executions)
    print(f"\nEjecuciones por workflow — últimos {args.days} días ({total} ejecuciones)\n")
    print(f"{'#':>3}  {'workflow':40} {'runs':>6} {'errores':>8} {'última':>20}  migrado")
    ranked = sorted(stats.items(), key=lambda kv: kv[1]["total"], reverse=True)
    for i, (wid, s) in enumerate(ranked, 1):
        meta = workflows.get(wid, {"name": f"(id {wid})", "active": False})
        name = meta["name"]
        slug = migrated.get(name.lower(), "")
        mark = f"✅ {slug}" if slug else ("—" if meta["active"] else "— (inactivo)")
        last = s["last"][:16].replace("T", " ")
        print(f"{i:>3}  {name[:40]:40} {s['total']:>6} {s['errors']:>8} {last:>20}  {mark}")

    pending = [
        (workflows.get(w, {}).get("name", w), s["total"])
        for w, s in ranked
        if workflows.get(w, {}).get("name", "").lower() not in migrated
    ]
    if pending:
        print("\nSiguientes candidatos a migrar (más usados sin migrar):")
        for name, total in pending[:5]:
            print(f"  - {name} ({total} runs)")


if __name__ == "__main__":
    main()
