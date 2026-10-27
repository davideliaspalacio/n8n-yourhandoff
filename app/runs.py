"""Registro de ejecuciones y de costes. Sustituye al execution-logger de n8n.

Cada petición a la API abre un run; cada paso del flujo y cada llamada de pago
cuelgan de él. Los clientes no reciben el run como parámetro: lo leen de un
`ContextVar` (`current_run`), así un subflujo cuesta lo mismo llamado solo o
desde el orquestador y su coste acaba siempre en el run que lo lanzó.

Con `DATABASE_URL` se guarda en Postgres (ver migrations/); sin ella, en
memoria, que basta para local y tests pero se pierde al reiniciar.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"


class RunStore(Protocol):
    async def create(
        self, flow: str, input: dict, *, dry_run: bool = False, replay_of: str | None = None
    ) -> str: ...
    async def step(self, run_id: str, name: str, status: str, detail: Any = None) -> None: ...
    async def finish(
        self, run_id: str, status: str, *, output: Any = None, error: str | None = None
    ) -> None: ...
    async def add_cost(
        self,
        run_id: str,
        provider: str,
        operation: str,
        units: float,
        cost_usd: Decimal,
        meta: dict | None = None,
    ) -> None: ...
    async def get(self, run_id: str) -> dict | None: ...
    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict: ...
    async def fail_orphans(self) -> int: ...
    async def close(self) -> None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _jsonable(value: Any) -> Any:
    """Pydantic, Decimal y datetime a JSON sin perder nada útil."""
    return json.loads(json.dumps(value, default=_default))


def _run_summary(run: dict) -> dict:
    """Campos de un run para listados: sin steps ni costes, que son del detalle."""
    keys = ("id", "flow", "status", "dry_run", "replay_of", "error", "created_at", "finished_at")
    return {k: run.get(k) for k in keys}


def _default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class MemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    async def create(
        self, flow: str, input: dict, *, dry_run: bool = False, replay_of: str | None = None
    ) -> str:
        run_id = str(uuid.uuid4())
        self._runs[run_id] = {
            "id": run_id,
            "flow": flow,
            "status": RUNNING,
            "dry_run": dry_run,
            "replay_of": replay_of,
            "input": _jsonable(input),
            "output": None,
            "error": None,
            "steps": [],
            "costs": [],
            "created_at": _now(),
            "finished_at": None,
        }
        return run_id

    async def step(self, run_id: str, name: str, status: str, detail: Any = None) -> None:
        self._runs[run_id]["steps"].append(
            {"name": name, "status": status, "detail": _jsonable(detail), "at": _now()}
        )

    async def finish(
        self, run_id: str, status: str, *, output: Any = None, error: str | None = None
    ) -> None:
        run = self._runs[run_id]
        run.update(status=status, output=_jsonable(output), error=error, finished_at=_now())

    async def add_cost(
        self,
        run_id: str,
        provider: str,
        operation: str,
        units: float,
        cost_usd: Decimal,
        meta: dict | None = None,
    ) -> None:
        self._runs[run_id]["costs"].append(
            {
                "provider": provider,
                "operation": operation,
                "units": units,
                "cost_usd": float(cost_usd),
                "meta": _jsonable(meta or {}),
                "at": _now(),
            }
        )

    async def get(self, run_id: str) -> dict | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        return {**run, "total_cost_usd": round(sum(c["cost_usd"] for c in run["costs"]), 6)}

    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        def keep(r: dict) -> bool:
            if flow and r["flow"] != flow:
                return False
            if status and r["status"] != status:
                return False
            # ISO-8601 UTC: comparar como texto equivale a comparar fechas.
            if since and r["created_at"] < since:
                return False
            if until and r["created_at"] > until:
                return False
            return True

        rows = sorted(
            (r for r in self._runs.values() if keep(r)),
            key=lambda r: r["created_at"],
            reverse=True,
        )
        start = (page - 1) * per_page
        return {
            "items": [_run_summary(r) for r in rows[start : start + per_page]],
            "total": len(rows),
            "page": page,
            "per_page": per_page,
        }

    async def fail_orphans(self) -> int:
        return 0

    async def close(self) -> None:
        return None


class PostgresRunStore:
    def __init__(self, dsn: str) -> None:
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(dsn, min_size=1, max_size=5, open=False)

    async def open(self) -> None:
        await self._pool.open()

    async def migrate(self) -> None:
        """Aplica migrations/*.sql en orden. Son idempotentes, así que corre en
        cada arranque y ningún despliegue depende de un paso manual."""
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            async with self._pool.connection() as conn:
                await conn.execute(path.read_text())

    async def close(self) -> None:
        await self._pool.close()

    async def _exec(self, sql: str, params: tuple) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(sql, params)

    async def create(
        self, flow: str, input: dict, *, dry_run: bool = False, replay_of: str | None = None
    ) -> str:
        run_id = str(uuid.uuid4())
        await self._exec(
            "insert into flow_runs (id, flow, status, dry_run, input, replay_of)"
            " values (%s,%s,%s,%s,%s,%s)",
            (run_id, flow, RUNNING, dry_run, json.dumps(_jsonable(input)), replay_of),
        )
        return run_id

    async def step(self, run_id: str, name: str, status: str, detail: Any = None) -> None:
        entry = {"name": name, "status": status, "detail": _jsonable(detail), "at": _now()}
        await self._exec(
            "update flow_runs set steps = steps || %s::jsonb where id = %s",
            (json.dumps([entry]), run_id),
        )

    async def finish(
        self, run_id: str, status: str, *, output: Any = None, error: str | None = None
    ) -> None:
        await self._exec(
            "update flow_runs set status=%s, output=%s, error=%s, finished_at=now() where id=%s",
            (status, json.dumps(_jsonable(output)), error, run_id),
        )

    async def add_cost(
        self,
        run_id: str,
        provider: str,
        operation: str,
        units: float,
        cost_usd: Decimal,
        meta: dict | None = None,
    ) -> None:
        await self._exec(
            "insert into cost_events (run_id, provider, operation, units, cost_usd, meta)"
            " values (%s,%s,%s,%s,%s,%s)",
            (run_id, provider, operation, units, cost_usd, json.dumps(_jsonable(meta or {}))),
        )

    async def get(self, run_id: str) -> dict | None:
        try:
            uuid.UUID(run_id)
        except ValueError:
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select id::text, flow, status, dry_run, replay_of::text, input, output, error,"
                " steps, created_at, finished_at from flow_runs where id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "select provider, operation, units::float, cost_usd::float, meta, created_at"
                " from cost_events where run_id = %s order by id",
                (run_id,),
            )
            costs = [
                {
                    "provider": c[0],
                    "operation": c[1],
                    "units": c[2],
                    "cost_usd": c[3],
                    "meta": c[4],
                    "at": c[5].isoformat(),
                }
                for c in await cur.fetchall()
            ]
        keys = ["id", "flow", "status", "dry_run", "replay_of", "input", "output", "error", "steps"]
        run = dict(zip(keys, row[:9], strict=True))
        run["created_at"] = row[9].isoformat()
        run["finished_at"] = row[10].isoformat() if row[10] else None
        run["costs"] = costs
        run["total_cost_usd"] = round(sum(c["cost_usd"] for c in costs), 6)
        return run

    async def list_runs(
        self,
        *,
        flow: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> dict:
        where, params = [], []
        if flow:
            where.append("flow = %s")
            params.append(flow)
        if status:
            where.append("status = %s")
            params.append(status)
        if since:
            where.append("created_at >= %s::timestamptz")
            params.append(since)
        if until:
            where.append("created_at <= %s::timestamptz")
            params.append(until)
        cond = (" where " + " and ".join(where)) if where else ""
        async with self._pool.connection() as conn:
            cur = await conn.execute(f"select count(*) from flow_runs{cond}", tuple(params))
            total = (await cur.fetchone())[0]
            cur = await conn.execute(
                "select id::text, flow, status, dry_run, replay_of::text, error,"
                f" created_at, finished_at from flow_runs{cond}"
                " order by created_at desc limit %s offset %s",
                tuple(params) + (per_page, (page - 1) * per_page),
            )
            items = [
                {
                    "id": r[0],
                    "flow": r[1],
                    "status": r[2],
                    "dry_run": r[3],
                    "replay_of": r[4],
                    "error": r[5],
                    "created_at": r[6].isoformat(),
                    "finished_at": r[7].isoformat() if r[7] else None,
                }
                for r in await cur.fetchall()
            ]
        return {"items": items, "total": total, "page": page, "per_page": per_page}

    async def fail_orphans(self) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "update flow_runs set status=%s, error=%s, finished_at=now() where status=%s",
                (FAILED, "Interrupted by a service restart", RUNNING),
            )
            return cur.rowcount


# --- Contexto del run en curso ---------------------------------------------


@dataclass(frozen=True)
class RunContext:
    store: RunStore
    run_id: str


current_run: ContextVar[RunContext | None] = ContextVar("current_run", default=None)


@contextmanager
def bind_run(store: RunStore, run_id: str):
    token = current_run.set(RunContext(store, run_id))
    try:
        yield
    finally:
        current_run.reset(token)


async def record_cost(
    provider: str,
    operation: str,
    units: float,
    cost_usd: Decimal,
    meta: dict | None = None,
) -> None:
    """Anota una llamada de pago en el run actual.

    La llamada ya está pagada: si falla la escritura, se deja en el log con todo
    lo necesario para reconstruirla y se sigue. Es un agujero visible, no uno
    silencioso.
    """
    ctx = current_run.get()
    if ctx is None:
        logger.warning(
            "cost without run: provider=%s op=%s units=%s cost_usd=%s",
            provider,
            operation,
            units,
            cost_usd,
        )
        return
    try:
        await ctx.store.add_cost(ctx.run_id, provider, operation, units, cost_usd, meta)
    except Exception:
        logger.critical(
            "LEDGER GAP: run=%s provider=%s op=%s units=%s cost_usd=%s meta=%s",
            ctx.run_id,
            provider,
            operation,
            units,
            cost_usd,
            meta,
            exc_info=True,
        )


async def record_step(name: str, status: str, detail: Any = None) -> None:
    ctx = current_run.get()
    if ctx is None:
        return
    try:
        await ctx.store.step(ctx.run_id, name, status, detail)
    except Exception:
        logger.exception("could not record step %s for run %s", name, ctx.run_id)
