#!/usr/bin/env python3
# scripts/catalog.py
"""Inventario vivo del repo, generado por introspección (nunca a mano).

Uso: uv run python scripts/catalog.py

Imprime en markdown compacto los flows registrados, los clients, las
funciones de dominio y los NOMBRES de los settings. Es el contexto que un
agente debe leer antes de construir o migrar un flow.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKIP_CLIENT_MODULES = {"base", "http", "__init__"}


def first_sentence(text: str | None) -> str:
    """Primera frase de un texto, en una sola línea."""
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    end = flat.find(". ")
    if end == -1:
        return flat.rstrip(".")
    return flat[:end]


def _doc_line(obj: object) -> str:
    doc = inspect.getdoc(obj) or ""
    return doc.split("\n")[0].strip()


def _sig(fn: object) -> str:
    try:
        return str(inspect.signature(fn, eval_str=True))  # type: ignore[arg-type]
    except Exception:
        try:
            return str(inspect.signature(fn))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "(...)"


def _modules(package: str) -> list[object]:
    pkg = importlib.import_module(package)
    mods = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if info.ispkg:
            continue
        mods.append((info.name, importlib.import_module(f"{package}.{info.name}")))
    return mods


def flows_section() -> tuple[list[str], int, int]:
    from app.flows.registry import discover

    specs = discover()
    lines: list[str] = []
    counts = {"workflow": 0, "node": 0}
    for kind, title in (("workflow", "Workflows"), ("node", "Nodes")):
        group = sorted((s for s in specs.values() if s.kind == kind), key=lambda s: s.slug)
        counts[kind] = len(group)
        lines += [f"## {title} ({len(group)})", ""]
        for s in group:
            lines.append(f"### {s.slug} — {s.mode}, POST /v1/flows/{s.slug}")
            if s.replaces:
                lines.append(f"- replaces: {s.replaces}")
            desc = first_sentence(s.description or s.summary)
            if desc:
                lines.append(f"- {desc}")
            for node in s.graph:
                to = ", ".join(node.get("to") or ()) or "fin"
                about = first_sentence(node.get("about"))
                lines.append(f"  - {node['id']} → {to}" + (f": {about}" if about else ""))
            lines.append("")
    return lines, counts["workflow"], counts["node"]


def clients_section() -> tuple[list[str], int]:
    from app.clients.base import BaseClient

    lines = ["## Clients (app/clients)", ""]
    n = 0
    for name, mod in _modules("app.clients"):
        if name in SKIP_CLIENT_MODULES:
            continue
        classes = [
            c
            for _, c in inspect.getmembers(mod, inspect.isclass)
            if c.__module__ == mod.__name__ and issubclass(c, BaseClient)
        ]
        for cls in classes:
            n += 1
            lines.append(f"### {cls.__name__} (app/clients/{name}.py)")
            for meth_name, fn in inspect.getmembers(cls, inspect.isfunction):
                if meth_name.startswith("_") or fn.__qualname__.split(".")[0] != cls.__name__:
                    continue
                doc = _doc_line(fn)
                lines.append(f"- {meth_name}{_sig(fn)}" + (f" — {doc}" if doc else ""))
            lines.append("")
    return lines, n


def domain_section() -> tuple[list[str], int]:
    lines = ["## Domain (app/domain)", ""]
    n = 0
    for name, mod in _modules("app.domain"):
        funcs = [
            f
            for fname, f in inspect.getmembers(mod, inspect.isfunction)
            if not fname.startswith("_") and f.__module__ == mod.__name__
        ]
        if not funcs:
            continue
        lines.append(f"### {name}.py")
        for f in funcs:
            n += 1
            doc = _doc_line(f)
            lines.append(f"- {f.__name__}{_sig(f)}" + (f" — {doc}" if doc else ""))
        lines.append("")
    return lines, n


def settings_section() -> list[str]:
    from app.config import Settings

    names = ", ".join(Settings.model_fields)
    return ["## Settings (solo nombres; env en MAYÚSCULAS)", "", names, ""]


def build() -> str:
    flow_lines, n_wf, n_node = flows_section()
    client_lines, n_clients = clients_section()
    domain_lines, n_domain = domain_section()
    header = [
        f"# Catálogo handoff-flows — {date.today().isoformat()}",
        "",
        f"{n_wf} workflows, {n_node} nodes, {n_clients} clients, {n_domain} funciones de dominio.",
        "",
    ]
    return "\n".join(
        header + flow_lines + client_lines + domain_lines + settings_section()
    ).rstrip() + "\n"


def main() -> None:
    sys.stdout.write(build())


if __name__ == "__main__":
    main()
