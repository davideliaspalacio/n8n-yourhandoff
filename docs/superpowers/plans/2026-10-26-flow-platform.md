# Plataforma de flujos — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir `handoff-flows` en la plataforma de workflows: cada flow es un paquete autodescrito (`FlowSpec`) que se monta solo como `POST /v1/flows/<slug>`, con un dashboard web en `/ui` (flujos, ejecuciones, costes, relanzar) y un scaffold para añadir flows nuevos.

**Architecture:** Monolito modular FastAPI. Un registro descubre los paquetes de `app/flows/` y monta un endpoint por `FlowSpec` con run/steps/coste/dry_run automáticos. El dashboard es HTML+JS estático servido por el mismo servicio, consumiendo 4 endpoints JSON nuevos que leen de `flow_runs`/`cost_events`. Spec aprobado: `docs/superpowers/specs/2026-10-26-flow-platform-design.md`.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, psycopg3, uv, pytest (asyncio auto) + respx, ruff. UI: HTML/CSS/JS vanilla, sin build step ni CDN.

## Global Constraints

- **Sin dependencias Python nuevas** (todo lo necesario ya está en `pyproject.toml`).
- Tras cada task: `uv run pytest` y `uv run ruff check .` en verde.
- Copy de la API (Swagger, mensajes) en **inglés**; código y comentarios en **español** (patrón del repo).
- Auth: todo `/v1` exige `X-API-Key` (fallar cerrado); `/health`, `/docs` y el shell de `/ui` son públicos.
- Migraciones SQL **idempotentes** (`create/alter ... if not exists`), se aplican al arrancar.
- Dentro de los paquetes de flows usar **imports absolutos** (`from app.clients import Clients`), no relativos de 3 niveles.
- Los commits terminan con la línea de atribución `Co-Authored-By` que indique el harness.

## Orquestación en paralelo (para el dispatcher)

| Ola | Tasks en paralelo | Notas |
|---|---|---|
| 1 | A1, B1 | Archivos disjuntos (A1: `app/flows/registry.py`; B1: `app/runs.py` + `migrations/`) |
| 2 | A2, B2 | A2 toca `app/api/` + `app/main.py`; B2 solo `app/runs.py` |
| 3 | A3, B3, D1 | A3: paquetes nuevos; B3: `app/runs.py`; D1: `scripts/` |
| 4 | A4, C1 | A4: paquetes + `app/api/routes.py`; C1: `app/ui/` + mount en `app/main.py` |
| 5 | A5, B4 | A5 cierra Track A; B4 necesita A2+B1–B3 |
| 6 | B5, D2 | B5 toca `app/main.py` (ya libre); D2 documenta lo terminado |

Regla: dos tasks solo van en paralelo si no tocan el mismo archivo. `app/main.py` lo tocan A2, C1 y B5 — siempre en olas distintas.

---

# Track A — FlowSpec, registro y migración de los 6 flows

### Task A1: `FlowSpec` y registro con descubrimiento

**Files:**
- Create: `app/flows/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: nada (solo Pydantic).
- Produces: `FlowSpec` (dataclass congelada: `slug, title, input_model, run, mode="sync", replaces="", summary="", response_model=None`), `validate(spec, *, origin)`, `add_specs(registry, specs, *, origin)`, `discover() -> dict[str, FlowSpec]`. Contrato de `run`: `async def run(clients: Clients, data: InputModel) -> Any`. Un paquete exporta `spec` (normal) o `specs` (varios workflows que comparten implementación, p. ej. CRM).

- [ ] **Step 1: Test que falla**

```python
# tests/test_registry.py
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.flows.registry import FlowSpec, add_specs, discover, validate


class EchoInput(BaseModel):
    text: str


async def _echo(clients, data):
    return {"echo": data.text}


def _spec(**overrides) -> FlowSpec:
    base = dict(slug="echo", title="Echo", input_model=EchoInput, run=_echo)
    return FlowSpec(**{**base, **overrides})


def test_valid_spec_passes():
    validate(_spec(), origin="test")


@pytest.mark.parametrize("slug", ["Echo", "e cho", "echo_", "-echo", ""])
def test_bad_slug_is_rejected(slug):
    with pytest.raises(RuntimeError, match="slug"):
        validate(_spec(slug=slug), origin="test")


def test_bad_mode_is_rejected():
    with pytest.raises(RuntimeError, match="mode"):
        validate(_spec(mode="batch"), origin="test")


def test_input_model_must_be_pydantic():
    with pytest.raises(RuntimeError, match="input_model"):
        validate(_spec(input_model=dict), origin="test")


def test_duplicate_slug_is_rejected():
    registry: dict[str, FlowSpec] = {}
    add_specs(registry, [_spec()], origin="a")
    with pytest.raises(RuntimeError, match="duplicado"):
        add_specs(registry, [_spec()], origin="b")


def test_discover_returns_registry():
    registry = discover()
    assert isinstance(registry, dict)
    for slug, spec in registry.items():
        assert slug == spec.slug
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/test_registry.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.flows.registry'`

- [ ] **Step 3: Implementación**

```python
# app/flows/registry.py
"""Registro de flows.

Cada paquete de app/flows/ con un flow.py que exporte `spec` (FlowSpec) —o
`specs` cuando varios workflows pequeños comparten implementación— queda
registrado y montado como POST /v1/flows/<slug>. Un paquete mal formado
impide arrancar: fallar en el deploy, no en producción.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODES = ("sync", "async")


@dataclass(frozen=True)
class FlowSpec:
    """Describe un flow: con esto el registro lo monta como endpoint y el
    dashboard lo lista, sin que el flow toque routes.py."""

    slug: str  # kebab-case; también es el nombre con el que se guarda el run
    title: str
    input_model: type[BaseModel]
    run: Callable[..., Awaitable[Any]]  # async def run(clients, data) -> Any
    mode: str = "sync"  # "sync": respuesta directa | "async": 202 + run_id
    replaces: str = ""  # workflow de n8n que sustituye
    summary: str = ""  # una línea para Swagger y el dashboard
    response_model: Any = None  # opcional, solo flows sync


def validate(spec: FlowSpec, *, origin: str) -> None:
    if not SLUG_RE.fullmatch(spec.slug or ""):
        raise RuntimeError(f"{origin}: slug inválido {spec.slug!r} (kebab-case: a-z, 0-9, '-')")
    if spec.mode not in MODES:
        raise RuntimeError(f"{origin}: mode inválido {spec.mode!r} (usa 'sync' o 'async')")
    if not (isinstance(spec.input_model, type) and issubclass(spec.input_model, BaseModel)):
        raise RuntimeError(f"{origin}: input_model debe ser un modelo Pydantic")


def add_specs(registry: dict[str, FlowSpec], specs: Iterable[FlowSpec], *, origin: str) -> None:
    for spec in specs:
        validate(spec, origin=origin)
        if spec.slug in registry:
            raise RuntimeError(f"slug duplicado {spec.slug!r} ({origin})")
        registry[spec.slug] = spec


def discover() -> dict[str, FlowSpec]:
    """Importa app/flows/<pkg>/flow.py de cada paquete y recoge sus specs.
    Los módulos sueltos (este mismo, o flows aún sin migrar) se ignoran."""
    import app.flows as flows_pkg

    registry: dict[str, FlowSpec] = {}
    for info in pkgutil.iter_modules(flows_pkg.__path__):
        if not info.ispkg:
            continue
        origin = f"app/flows/{info.name}/flow.py"
        module = importlib.import_module(f"app.flows.{info.name}.flow")
        specs = getattr(module, "specs", None)
        if specs is None:
            spec = getattr(module, "spec", None)
            specs = (spec,) if spec is not None else ()
        if not specs:
            raise RuntimeError(f"{origin} no exporta `spec` ni `specs`")
        add_specs(registry, specs, origin=origin)
    return registry
```

- [ ] **Step 4: Verificar que pasa**

Run: `uv run pytest tests/test_registry.py -v && uv run ruff check .`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/flows/registry.py tests/test_registry.py
git commit -m "feat: FlowSpec y registro de flows con descubrimiento por paquete"
```

---

### Task A2: Router genérico `/v1/flows/<slug>` y wiring en `main.py`

**Files:**
- Create: `app/api/flow_routes.py`
- Modify: `app/main.py` (param `flow_registry` en `create_app`, `app.state.flow_registry`, incluir router)
- Test: `tests/test_flow_routes.py`

**Interfaces:**
- Consumes: `FlowSpec`, `discover` (A1); `_run_sync`, `ERRORS`, `clients_dep` de `app/api/routes.py`; `require_api_key`, `run_store` de `app/api/deps.py`.
- Produces: `RunAccepted(run_id, status, status_url)`; `async launch_run(spec, data, *, request, store, clients, replay_of=None) -> RunAccepted` (B4 lo reutiliza para replay; solo pasa `replay_of` a `store.create` si no es None, así no depende de B1); `build_flows_router(registry) -> APIRouter`. `create_app(settings=None, *, clients=None, store=None, flow_registry=None)` — `flow_registry=None` ⇒ `discover()`.

- [ ] **Step 1: Test que falla**

```python
# tests/test_flow_routes.py
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.api.flow_routes import build_flows_router
from app.flows.registry import FlowSpec
from app.runs import MemoryRunStore, record_step

H = {"X-API-Key": "test-key"}


class EchoInput(BaseModel):
    text: str
    dry_run: bool = False


class BoomInput(BaseModel):
    pass


async def _echo(clients, data: EchoInput) -> dict:
    await record_step("echo", "succeeded", {"text": data.text})
    return {"echo": data.text}


async def _boom(clients, data: BoomInput) -> dict:
    raise ValueError("kaput")


ECHO = FlowSpec(slug="echo", title="Echo", input_model=EchoInput, run=_echo, mode="sync")
ECHO_BG = FlowSpec(slug="echo-bg", title="Echo BG", input_model=EchoInput, run=_echo, mode="async")
BOOM = FlowSpec(slug="boom", title="Boom", input_model=BoomInput, run=_boom, mode="sync")


@pytest.fixture
async def client(settings):
    app = FastAPI()
    app.state.settings = settings
    app.state.run_store = MemoryRunStore()
    app.state.clients = None
    app.state.background_tasks = set()
    app.include_router(build_flows_router({s.slug: s for s in (ECHO, ECHO_BG, BOOM)}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, app.state.run_store


async def test_sync_flow_runs_and_records(client):
    c, store = client
    r = await c.post("/v1/flows/echo", json={"text": "hola"}, headers=H)
    assert r.status_code == 200 and r.json() == {"echo": "hola"}
    run = await store.get(r.headers["X-Run-Id"])
    assert run["status"] == "succeeded" and run["flow"] == "echo"
    assert run["steps"][0]["name"] == "echo"


async def test_async_flow_returns_202_and_finishes(client):
    c, store = client
    r = await c.post("/v1/flows/echo-bg", json={"text": "bg", "dry_run": True}, headers=H)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert r.json()["status_url"] == f"/v1/runs/{run_id}"
    for _ in range(50):
        run = await store.get(run_id)
        if run["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert run["status"] == "succeeded" and run["dry_run"] is True


async def test_sync_flow_error_marks_run_failed(client):
    c, store = client
    r = await c.post("/v1/flows/boom", json={}, headers=H)
    assert r.status_code == 422
    run = await store.get(r.headers["X-Run-Id"])
    assert run["status"] == "failed" and "kaput" in run["error"]


async def test_requires_api_key(client):
    c, _ = client
    assert (await c.post("/v1/flows/echo", json={"text": "x"})).status_code == 401
```

- [ ] **Step 2: Verificar que falla**

Run: `uv run pytest tests/test_flow_routes.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.api.flow_routes'`

- [ ] **Step 3: Implementación de `flow_routes.py`**

```python
# app/api/flow_routes.py
"""Monta cada FlowSpec del registro como POST /v1/flows/<slug>, con run,
steps, coste y dry_run automáticos: lo que el orquestador hacía a mano."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel

from ..clients import Clients
from ..flows.registry import FlowSpec
from ..runs import FAILED, SUCCEEDED, RunStore, bind_run
from .deps import require_api_key, run_store
from .routes import ERRORS, _run_sync, clients_dep

logger = logging.getLogger(__name__)


class RunAccepted(BaseModel):
    run_id: str
    status: str
    status_url: str


async def launch_run(
    spec: FlowSpec,
    data: BaseModel,
    *,
    request: Request,
    store: RunStore,
    clients: Clients,
    replay_of: str | None = None,
) -> RunAccepted:
    # replay_of solo se pasa si existe, para no exigir que el store lo soporte.
    kwargs: dict = {"dry_run": bool(getattr(data, "dry_run", False))}
    if replay_of is not None:
        kwargs["replay_of"] = replay_of
    run_id = await store.create(spec.slug, data.model_dump(), **kwargs)

    async def work() -> None:
        with bind_run(store, run_id):
            try:
                result = await spec.run(clients, data)
            except Exception as exc:
                logger.exception("flow %s run %s failed", spec.slug, run_id)
                await store.finish(run_id, FAILED, error=f"{type(exc).__name__}: {exc}")
            else:
                await store.finish(run_id, SUCCEEDED, output=result)

    task = asyncio.create_task(work())
    # Referencia fuerte: asyncio solo guarda referencias débiles a las tareas.
    tasks: set = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return RunAccepted(run_id=run_id, status="running", status_url=f"/v1/runs/{run_id}")


def _make_async_endpoint(spec: FlowSpec):
    async def endpoint(
        data: BaseModel,
        request: Request,
        store: RunStore = Depends(run_store),
        clients: Clients = Depends(clients_dep),
    ) -> RunAccepted:
        return await launch_run(spec, data, request=request, store=store, clients=clients)

    endpoint.__annotations__["data"] = spec.input_model
    return endpoint


def _make_sync_endpoint(spec: FlowSpec):
    async def endpoint(
        data: BaseModel,
        response: Response,
        store: RunStore = Depends(run_store),
        clients: Clients = Depends(clients_dep),
    ):
        return await _run_sync(store, response, spec.slug, data, lambda: spec.run(clients, data))

    endpoint.__annotations__["data"] = spec.input_model
    return endpoint


def build_flows_router(registry: dict[str, FlowSpec]) -> APIRouter:
    router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])
    for spec in registry.values():
        common = dict(
            responses=ERRORS,
            tags=["Flows"],
            summary=spec.summary or spec.title,
            description=(
                f"Replaces the n8n workflow *{spec.replaces}*." if spec.replaces else None
            ),
            name=f"flow_{spec.slug.replace('-', '_')}",
        )
        if spec.mode == "async":
            route = router.post(
                f"/flows/{spec.slug}",
                status_code=status.HTTP_202_ACCEPTED,
                response_model=RunAccepted,
                **common,
            )
            route(_make_async_endpoint(spec))
        else:
            route = router.post(
                f"/flows/{spec.slug}", response_model=spec.response_model, **common
            )
            route(_make_sync_endpoint(spec))
    return router
```

- [ ] **Step 4: Wiring en `main.py`**

En `app/main.py`:

```python
# imports nuevos
from .api.flow_routes import build_flows_router
from .flows.registry import FlowSpec, discover
```

Cambiar la firma de `create_app` y el cuerpo donde se incluye el router:

```python
def create_app(
    settings: Settings | None = None,
    *,
    clients: Clients | None = None,
    store: RunStore | None = None,
    flow_registry: dict[str, FlowSpec] | None = None,
) -> FastAPI:
```

y tras `app.state.settings = settings`:

```python
    registry = flow_registry if flow_registry is not None else discover()
    app.state.flow_registry = registry
    app.include_router(router)
    app.include_router(build_flows_router(registry))
```

(la línea `app.include_router(router)` ya existe; se añade la segunda).

- [ ] **Step 5: Verificar**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS — los 41 tests previos siguen verdes (con el árbol aún sin paquetes, `discover()` devuelve `{}` y el router genérico queda vacío) + los 4 nuevos.

- [ ] **Step 6: Commit**

```bash
git add app/api/flow_routes.py app/main.py tests/test_flow_routes.py
git commit -m "feat: router genérico que monta cada FlowSpec como POST /v1/flows/<slug>"
```

---

### Task A3: Paquetes `company`, `attendees` y `team_communications`

**Files:**
- Create: `app/flows/company/{__init__.py,flow.py,schemas.py,README.md}` (ídem `attendees/`, `team_communications/`)
- Delete: `app/flows/company.py`, `app/flows/attendees.py`, `app/flows/team_communications.py`
- Modify: `tests/test_registry.py` (asserts de slugs)
- Test: suite completa (los imports externos no cambian)

**Interfaces:**
- Consumes: `FlowSpec` (A1).
- Produces: paquetes con `spec`; `app.flows.company` etc. siguen exportando los mismos símbolos públicos que hoy (los `__init__.py` re-exportan), así `routes.py`, `discovery_call.py` y los tests no cambian.

Receta por flow (mecánica, la misma para los tres):

1. `mkdir app/flows/<nombre>` y `git mv app/flows/<nombre>.py app/flows/<nombre>/flow.py`.
2. En `flow.py`: cambiar imports relativos `..x` por absolutos `from app.x import ...` (el módulo bajó un nivel).
3. Crear `schemas.py` y mover ahí las clases Pydantic públicas (si alguna depende de helpers del flow, se queda en `flow.py`; la regla dura es solo que `spec` viva en `flow.py`). `flow.py` las importa con `from .schemas import ...`.
4. Añadir al final de `flow.py` el wrapper `run` y el `spec` (código exacto abajo).
5. `__init__.py` re-exporta todo lo que se importaba del módulo viejo. Verificar con `grep -rn "flows.<nombre> import" app tests`.
6. `README.md`: 15–30 líneas — qué hace, qué clients/flows reusa, qué workflow de n8n sustituye, qué verificar con credenciales reales.

- [ ] **Step 1: Test que falla** — añadir a `tests/test_registry.py`:

```python
def test_research_flows_are_registered():
    registry = discover()
    assert {"enrich-company", "enrich-contacts", "team-communications"} <= set(registry)
    assert registry["enrich-company"].mode == "sync"
```

Run: `uv run pytest tests/test_registry.py::test_research_flows_are_registered -v`
Expected: FAIL (slugs no registrados)

- [ ] **Step 2: Paquete `company`** — specs exactos a añadir en `app/flows/company/flow.py`:

```python
from app.flows.registry import FlowSpec

from .schemas import CompanyEnrichment, CompanyRequest  # movidas a schemas.py


async def run(clients: Clients, data: CompanyRequest) -> CompanyEnrichment:
    return await enrich_company(clients, data.companyUrl)


spec = FlowSpec(
    slug="enrich-company",
    title="Enrich Company",
    input_model=CompanyRequest,
    response_model=CompanyEnrichment,
    run=run,
    replaces="Enrich Company (+ Get LinkedIn Data, News + Job Search)",
    summary="Enrich a company (Apollo, Google News, Google Jobs) and summarize it",
)
```

`app/flows/company/__init__.py`:

```python
from .flow import enrich_company, linkedin_uid, map_organization, run, spec
from .schemas import CompanyEnrichment, CompanyRequest

__all__ = [
    "CompanyEnrichment", "CompanyRequest", "enrich_company",
    "linkedin_uid", "map_organization", "run", "spec",
]
```

(ajustar la lista según lo que el `grep` del paso 5 de la receta encuentre realmente importado).

- [ ] **Step 3: Paquete `attendees`** — spec en `app/flows/attendees/flow.py`:

```python
async def run(clients: Clients, data: ContactsRequest) -> list[EnrichedContact]:
    return await enrich_contacts(clients, data.emails)


spec = FlowSpec(
    slug="enrich-contacts",
    title="Enrich External Attendees",
    input_model=ContactsRequest,
    response_model=list[EnrichedContact],
    run=run,
    replaces="Enrich External Attendees",
    summary="Enrich attendees with Apollo and write a pre-call bio for each",
)
```

- [ ] **Step 4: Paquete `team_communications`** — spec:

```python
async def run(clients: Clients, data: CommunicationsRequest) -> CommunicationsResult:
    return await search_team_communications(clients, data.emails)


spec = FlowSpec(
    slug="team-communications",
    title="Search Team Communications",
    input_model=CommunicationsRequest,
    response_model=CommunicationsResult,
    run=run,
    replaces="Search Team Communications (+ Fireflies)",
    summary="Search team communications (Gmail via Unipile + Fireflies) and summarize",
)
```

- [ ] **Step 5: Verificar**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS completo. En Swagger (arrancando en local) aparecen `/v1/flows/enrich-company`, `/v1/flows/enrich-contacts`, `/v1/flows/team-communications` además de los alias viejos.

- [ ] **Step 6: Commit**

```bash
git add -A app/flows tests/test_registry.py
git commit -m "feat: company, attendees y team_communications como paquetes con FlowSpec"
```

---

### Task A4: Paquetes `crm` (3 specs) y `discovery_call` (async)

**Files:**
- Create: `app/flows/crm/{__init__.py,flow.py,schemas.py,README.md}`, `app/flows/discovery_call/{__init__.py,flow.py,schemas.py,README.md}`
- Delete: `app/flows/crm.py`, `app/flows/discovery_call.py`
- Modify: `app/api/routes.py` (borrar el endpoint manual del orquestador y su `RunAccepted`; el alias `/crm/notes` usa `run_note`), `tests/test_registry.py`
- Test: suite completa

**Interfaces:**
- Consumes: receta de A3; `run_discovery_call`, `upsert_company`, `upsert_contact` existentes.
- Produces: `app.flows.crm` exporta además `run_note(clients, data: NoteInput) -> NoteResult`; el registro gana `crm-company`, `crm-contact`, `crm-note`, `discovery-call-booked`. `POST /v1/flows/discovery-call-booked` pasa a servirlo el router genérico con el mismo contrato (202 + `RunAccepted`).

- [ ] **Step 1: Test que falla** — añadir a `tests/test_registry.py`:

```python
def test_crm_and_orchestrator_flows_are_registered():
    registry = discover()
    assert {"crm-company", "crm-contact", "crm-note", "discovery-call-booked"} <= set(registry)
    assert registry["discovery-call-booked"].mode == "async"
```

- [ ] **Step 2: Paquete `crm`** — aplicar la receta a `crm.py`; al final de `flow.py`:

```python
async def run_company_crm(clients: Clients, data: CompanyCrmInput) -> CompanyCrmResult:
    return await upsert_company(clients, data)


async def run_contact_crm(clients: Clients, data: ContactCrmInput) -> ContactCrmResult:
    return await upsert_contact(clients, data)


async def run_note(clients: Clients, data: NoteInput) -> NoteResult:
    # Antes vivía inline en routes.py; ahora es un flow como los demás.
    note_id = await clients.hubspot.create_note(
        data.content, data.contact_company_deal, data.associated_hubspot_id
    )
    return NoteResult(noteId=note_id)


specs = (
    FlowSpec(
        slug="crm-company",
        title="Company CRM Add/Update",
        input_model=CompanyCrmInput,
        response_model=CompanyCrmResult,
        run=run_company_crm,
        replaces="Company CRM Add/Update",
        summary="Create or update a HubSpot company (matched by domain) and post its AI note",
    ),
    FlowSpec(
        slug="crm-contact",
        title="Contact CRM Add/Update",
        input_model=ContactCrmInput,
        response_model=ContactCrmResult,
        run=run_contact_crm,
        replaces="Contact CRM Add/Update",
        summary="Create or update a HubSpot contact (by email) and link it to a company",
    ),
    FlowSpec(
        slug="crm-note",
        title="Post Note to HubSpot",
        input_model=NoteInput,
        response_model=NoteResult,
        run=run_note,
        replaces="Post Note to Hubspot",
        summary="Post a note to a HubSpot company or contact",
    ),
)
```

- [ ] **Step 3: Paquete `discovery_call`** — receta + spec:

```python
async def run(clients: Clients, data: DiscoveryCallInput) -> dict:
    return await run_discovery_call(clients, data)


spec = FlowSpec(
    slug="discovery-call-booked",
    title="Discovery Call Booked",
    input_model=DiscoveryCallInput,
    run=run,
    mode="async",
    replaces="Discovery Call Booked",
    summary="Run the Discovery Call Booked flow",
)
```

- [ ] **Step 4: Limpiar `routes.py`**

- Borrar el endpoint `discovery_call_booked` completo (líneas del decorador `@router.post("/flows/discovery-call-booked", ...)` hasta su `return`), el modelo `RunAccepted` y los imports que queden sin uso (`asyncio`, `DiscoveryCallInput`, `run_discovery_call`). El router genérico ya sirve ese path con el mismo contrato.
- En el alias `/crm/notes`, sustituir el `work` inline por:

```python
from ..flows.crm import run_note  # junto a los demás imports de flows

    return await _run_sync(store, response, "crm_note", data, lambda: run_note(clients, data))
```

- [ ] **Step 5: Verificar**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS. `test_discovery_call_runs_in_background` sigue verde contra el router genérico. Ojo: ese run ahora se guarda con flow `discovery-call-booked` (antes `discovery_call_booked`); si algún test asserta el nombre viejo, actualizarlo (`grep -rn "discovery_call_booked" tests/`).

- [ ] **Step 6: Commit**

```bash
git add -A app/flows app/api/routes.py tests
git commit -m "feat: crm y discovery_call como paquetes; el orquestador lo sirve el router genérico"
```

---

### Task A5: Aliases con slugs y verificación integral del track

**Files:**
- Modify: `app/api/routes.py` (strings de nombre de flow), tests que asserten nombres viejos
- Test: `tests/test_api.py` (test nuevo de equivalencia alias/genérico)

**Interfaces:**
- Consumes: todo el track A.
- Produces: los runs se guardan siempre con el slug del flow (`team-communications`, no `team_communications`), de modo que el dashboard agrupe igual los runs del alias y los del endpoint genérico.

- [ ] **Step 1: Renombrar los flows de los aliases**

En `routes.py`, en las llamadas `_run_sync(store, response, "<nombre>", ...)`:
`"team_communications"` → `"team-communications"`, `"enrich_contacts"` → `"enrich-contacts"`, `"enrich_company"` → `"enrich-company"`, `"crm_company"` → `"crm-company"`, `"crm_contact"` → `"crm-contact"`, `"crm_note"` → `"crm-note"`.
Después `grep -rn '"enrich_company"\|"team_communications"\|"crm_company"\|"crm_contact"\|"crm_note"\|"enrich_contacts"' tests/` y actualizar los asserts que encuentre.

- [ ] **Step 2: Test de equivalencia** — añadir a `tests/test_api.py`:

```python
async def test_generic_route_matches_alias(api, mock):
    World(mock)
    alias = await api.post("/v1/companies/enrich", json={"companyUrl": "staynido.com"}, headers=H)
    World(mock)
    generic = await api.post(
        "/v1/flows/enrich-company", json={"companyUrl": "staynido.com"}, headers=H
    )
    assert generic.status_code == 200
    assert generic.json() == alias.json()
    run = (await api.get(f"/v1/runs/{generic.headers['X-Run-Id']}", headers=H)).json()
    assert run["flow"] == "enrich-company" and run["status"] == "succeeded"
```

- [ ] **Step 3: Verificar y commitear**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS completo.

```bash
git add app/api/routes.py tests
git commit -m "feat: los aliases registran runs con el slug del flow"
```

---

# Track B — Observabilidad: datos, API y logging

### Task B1: `replay_of` en runs (migración 002 + stores)

**Files:**
- Create: `migrations/002_replay_of.sql`
- Modify: `app/runs.py` (Protocol + ambos stores), `tests/test_postgres_store.py`
- Test: `tests/test_runs_store.py` (nuevo)

**Interfaces:**
- Consumes: nada.
- Produces: `store.create(flow, input, *, dry_run=False, replay_of: str | None = None)`; los dicts de `get()` y los rows de listado incluyen `"replay_of"`.

- [ ] **Step 1: Test que falla**

```python
# tests/test_runs_store.py
from __future__ import annotations

from app.runs import MemoryRunStore


async def test_replay_of_round_trip():
    store = MemoryRunStore()
    original = await store.create("echo", {"text": "hola"})
    replay = await store.create("echo", {"text": "hola"}, replay_of=original)
    assert (await store.get(replay))["replay_of"] == original
    assert (await store.get(original))["replay_of"] is None
```

Run: `uv run pytest tests/test_runs_store.py -v`
Expected: FAIL con `TypeError: ... unexpected keyword argument 'replay_of'`

- [ ] **Step 2: Migración**

```sql
-- migrations/002_replay_of.sql
-- Un run relanzado desde el dashboard apunta al run original. Idempotente.
alter table flow_runs
    add column if not exists replay_of uuid references flow_runs (id) on delete set null;
```

- [ ] **Step 3: Implementación en `app/runs.py`**

- Protocol: `async def create(self, flow: str, input: dict, *, dry_run: bool = False, replay_of: str | None = None) -> str: ...`
- `MemoryRunStore.create`: misma firma; añadir `"replay_of": replay_of,` al dict del run.
- `PostgresRunStore.create`:

```python
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
```

- `PostgresRunStore.get`: añadir `replay_of::text` al select (tras `dry_run`) y `"replay_of"` a la lista `keys` en la misma posición.

- [ ] **Step 4: Test de Postgres** — en `tests/test_postgres_store.py`, siguiendo el patrón existente del archivo (skip sin `TEST_DATABASE_URL`), añadir un test que haga el mismo round-trip de `replay_of` contra Postgres.

- [ ] **Step 5: Verificar y commitear**

Run: `uv run pytest -v && uv run ruff check .`

```bash
git add migrations/002_replay_of.sql app/runs.py tests
git commit -m "feat: replay_of enlaza un run relanzado con su original"
```

---

### Task B2: `list_runs` en ambos stores

**Files:**
- Modify: `app/runs.py`
- Test: `tests/test_runs_store.py`

**Interfaces:**
- Consumes: B1 (columna/campo `replay_of`).
- Produces: `await store.list_runs(*, flow=None, status=None, since=None, until=None, page=1, per_page=50) -> dict` con `{"items": [...], "total": int, "page": int, "per_page": int}`. Cada item: `id, flow, status, dry_run, replay_of, error, created_at, finished_at` (sin steps/costes: eso es el detalle). `since`/`until` son ISO-8601 UTC. Orden: `created_at` desc.

- [ ] **Step 1: Tests que fallan** — añadir a `tests/test_runs_store.py`:

```python
async def _seed(store: MemoryRunStore) -> list[str]:
    ids = []
    for i in range(3):
        rid = await store.create("echo", {"i": i})
        await store.finish(rid, "succeeded" if i % 2 == 0 else "failed")
        ids.append(rid)
    ids.append(await store.create("otro", {}))
    return ids


async def test_list_runs_filters_and_paginates():
    store = MemoryRunStore()
    ids = await _seed(store)
    page = await store.list_runs(flow="echo")
    assert page["total"] == 3 and len(page["items"]) == 3
    assert page["items"][0]["id"] == ids[2]  # más reciente primero
    assert set(page["items"][0]) == {
        "id", "flow", "status", "dry_run", "replay_of", "error", "created_at", "finished_at",
    }
    failed = await store.list_runs(flow="echo", status="failed")
    assert failed["total"] == 1
    p2 = await store.list_runs(flow="echo", page=2, per_page=2)
    assert p2["total"] == 3 and len(p2["items"]) == 1


async def test_list_runs_since_until():
    store = MemoryRunStore()
    await _seed(store)
    cutoff = (await store.list_runs())["items"][0]["created_at"]
    assert (await store.list_runs(since=cutoff))["total"] == 1
    assert (await store.list_runs(until=cutoff))["total"] == 4
```

- [ ] **Step 2: Implementación**

`MemoryRunStore`:

```python
def _run_summary(run: dict) -> dict:
    keys = ("id", "flow", "status", "dry_run", "replay_of", "error", "created_at", "finished_at")
    return {k: run.get(k) for k in keys}
```

(función módulo-level, junto a `_jsonable`), y en la clase:

```python
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
```

`PostgresRunStore`:

```python
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
```

Añadir `list_runs` también al Protocol `RunStore`.

- [ ] **Step 3: Verificar, test Postgres equivalente en `test_postgres_store.py`, commit**

Run: `uv run pytest -v && uv run ruff check .`

```bash
git add app/runs.py tests
git commit -m "feat: list_runs con filtros y paginación en ambos stores"
```

---

### Task B3: `flow_stats` y `costs_summary`

**Files:**
- Modify: `app/runs.py`
- Test: `tests/test_runs_store.py`

**Interfaces:**
- Consumes: B2.
- Produces:
  - `await store.flow_stats(*, days=7) -> dict[str, dict]` — por flow: `{"runs": int, "succeeded": int, "cost_usd": float, "last_run_at": str | None, "last_status": str | None}` (`runs/succeeded/cost_usd` dentro de la ventana; `last_*` de siempre).
  - `await store.costs_summary(*, group_by="day", days=30) -> list[dict]` — items `{"day": "YYYY-MM-DD", "cost_usd": float}` o `{"provider": str, "cost_usd": float}`; por día orden desc, por proveedor orden alfabético.

- [ ] **Step 1: Tests que fallan** — añadir a `tests/test_runs_store.py`:

```python
from decimal import Decimal


async def test_flow_stats_and_costs_summary():
    store = MemoryRunStore()
    ok = await store.create("echo", {})
    await store.add_cost(ok, "openai", "chat", 1000, Decimal("0.01"))
    await store.finish(ok, "succeeded")
    bad = await store.create("echo", {})
    await store.add_cost(bad, "apollo", "enrich", 1, Decimal("0.02"))
    await store.finish(bad, "failed")

    stats = await store.flow_stats(days=7)
    assert stats["echo"]["runs"] == 2 and stats["echo"]["succeeded"] == 1
    assert stats["echo"]["last_status"] == "failed"
    assert stats["echo"]["cost_usd"] == 0.03

    by_provider = await store.costs_summary(group_by="provider")
    assert by_provider == [
        {"provider": "apollo", "cost_usd": 0.02},
        {"provider": "openai", "cost_usd": 0.01},
    ]
    by_day = await store.costs_summary(group_by="day")
    assert len(by_day) == 1 and by_day[0]["cost_usd"] == 0.03
```

- [ ] **Step 2: Implementación**

`MemoryRunStore` (necesita `from datetime import timedelta` ya disponible vía `datetime`; añadir al import existente):

```python
    async def flow_stats(self, *, days: int = 7) -> dict[str, dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        stats: dict[str, dict] = {}
        for run in sorted(self._runs.values(), key=lambda r: r["created_at"]):
            s = stats.setdefault(
                run["flow"],
                {"runs": 0, "succeeded": 0, "cost_usd": 0.0, "last_run_at": None, "last_status": None},
            )
            s["last_run_at"] = run["created_at"]
            s["last_status"] = run["status"]
            if run["created_at"] >= cutoff:
                s["runs"] += 1
                if run["status"] == SUCCEEDED:
                    s["succeeded"] += 1
                s["cost_usd"] = round(s["cost_usd"] + sum(c["cost_usd"] for c in run["costs"]), 6)
        return stats

    async def costs_summary(self, *, group_by: str = "day", days: int = 30) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        buckets: dict[str, float] = {}
        for run in self._runs.values():
            for c in run["costs"]:
                if c["at"] < cutoff:
                    continue
                key = c["at"][:10] if group_by == "day" else c["provider"]
                buckets[key] = round(buckets.get(key, 0.0) + c["cost_usd"], 6)
        key_name = "day" if group_by == "day" else "provider"
        ordered = sorted(buckets.items(), reverse=(group_by == "day"))
        return [{key_name: k, "cost_usd": v} for k, v in ordered]
```

`PostgresRunStore`:

```python
    async def flow_stats(self, *, days: int = 7) -> dict[str, dict]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select flow,"
                " count(*) filter (where created_at > now() - make_interval(days => %s)),"
                " count(*) filter (where status = 'succeeded'"
                "   and created_at > now() - make_interval(days => %s)),"
                " max(created_at),"
                " (array_agg(status order by created_at desc))[1]"
                " from flow_runs group by flow",
                (days, days),
            )
            rows = await cur.fetchall()
            cur = await conn.execute(
                "select r.flow, sum(c.cost_usd)::float from flow_runs r"
                " join cost_events c on c.run_id = r.id"
                " where c.created_at > now() - make_interval(days => %s) group by r.flow",
                (days,),
            )
            costs = dict(await cur.fetchall())
        return {
            flow: {
                "runs": runs,
                "succeeded": succeeded,
                "cost_usd": round(costs.get(flow, 0.0), 6),
                "last_run_at": last_at.isoformat(),
                "last_status": last_status,
            }
            for flow, runs, succeeded, last_at, last_status in rows
        }

    async def costs_summary(self, *, group_by: str = "day", days: int = 30) -> list[dict]:
        if group_by == "day":
            col, order = "to_char(created_at at time zone 'UTC', 'YYYY-MM-DD')", "1 desc"
        else:
            col, order = "provider", "1 asc"
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"select {col} as k, round(sum(cost_usd), 6)::float from cost_events"
                " where created_at > now() - make_interval(days => %s)"
                f" group by 1 order by {order}",
                (days,),
            )
            rows = await cur.fetchall()
        key_name = "day" if group_by == "day" else "provider"
        return [{key_name: k, "cost_usd": v} for k, v in rows]
```

Añadir ambos métodos al Protocol. (`round(sum(...), 6)` en SQL opera sobre numeric; el `::float` va después.)

- [ ] **Step 3: Verificar, tests Postgres equivalentes, commit**

Run: `uv run pytest -v && uv run ruff check .`

```bash
git add app/runs.py tests
git commit -m "feat: flow_stats y costs_summary para el dashboard"
```

---

### Task B4: Router del dashboard — `GET /v1/flows`, `GET /v1/runs`, `GET /v1/costs/summary`, `POST /v1/runs/{id}/replay`

**Files:**
- Create: `app/api/dashboard.py`
- Modify: `app/main.py` (incluir router + tag OpenAPI `Dashboard`)
- Test: `tests/test_dashboard_api.py`

**Interfaces:**
- Consumes: `flow_stats`, `list_runs`, `costs_summary` (B1–B3); `launch_run`, `RunAccepted` (A2); `app.state.flow_registry` (A2).
- Produces (contrato que consume la UI de C1):
  - `GET /v1/flows` → `{"flows": [{"slug", "title", "mode", "replaces", "endpoint", "stats": {"runs", "succeeded", "cost_usd", "last_run_at", "last_status"}}]}` ordenado por slug.
  - `GET /v1/runs?flow=&status=&since=&until=&page=1&per_page=50` → lo que devuelve `list_runs`.
  - `GET /v1/costs/summary?group_by=day|provider&days=30` → `{"items": [...]}`.
  - `POST /v1/runs/{run_id}/replay` → 202 `RunAccepted` (siempre en segundo plano; el run nuevo lleva `replay_of`). 404 si no existe el run, 409 si su flow ya no está registrado, 422 si el input guardado ya no valida.

- [ ] **Step 1: Tests que fallan**

```python
# tests/test_dashboard_api.py
from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel

from app.flows.registry import FlowSpec
from app.main import create_app
from app.runs import MemoryRunStore

H = {"X-API-Key": "test-key"}


class EchoInput(BaseModel):
    text: str
    dry_run: bool = False


async def _echo(clients, data: EchoInput) -> dict:
    return {"echo": data.text}


ECHO = FlowSpec(slug="echo", title="Echo", input_model=EchoInput, run=_echo, replaces="Echo v1")


@pytest.fixture
async def dash(settings, clients):
    store = MemoryRunStore()
    app = create_app(settings, clients=clients, store=store, flow_registry={"echo": ECHO})
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c, store


async def test_flows_lists_registry_with_stats(dash):
    c, store = dash
    rid = await c.post("/v1/flows/echo", json={"text": "x"}, headers=H)
    assert rid.status_code == 200
    body = (await c.get("/v1/flows", headers=H)).json()
    [flow] = body["flows"]
    assert flow["slug"] == "echo" and flow["endpoint"] == "/v1/flows/echo"
    assert flow["stats"]["runs"] == 1 and flow["stats"]["succeeded"] == 1


async def test_runs_listing_and_costs(dash):
    c, store = dash
    rid = await store.create("echo", {"text": "x"})
    await store.add_cost(rid, "openai", "chat", 10, Decimal("0.05"))
    await store.finish(rid, "failed", error="boom")
    runs = (await c.get("/v1/runs?flow=echo&status=failed", headers=H)).json()
    assert runs["total"] == 1 and runs["items"][0]["error"] == "boom"
    costs = (await c.get("/v1/costs/summary?group_by=provider", headers=H)).json()
    assert costs["items"] == [{"provider": "openai", "cost_usd": 0.05}]


async def test_replay_reruns_with_link(dash):
    c, store = dash
    rid = await store.create("echo", {"text": "otra vez"})
    await store.finish(rid, "failed", error="boom")
    r = await c.post(f"/v1/runs/{rid}/replay", headers=H)
    assert r.status_code == 202
    new_id = r.json()["run_id"]
    for _ in range(50):
        run = await store.get(new_id)
        if run["status"] != "running":
            break
        await asyncio.sleep(0.02)
    assert run["status"] == "succeeded" and run["replay_of"] == rid


async def test_replay_of_unknown_run_is_404(dash):
    c, _ = dash
    assert (await c.post("/v1/runs/00000000-0000-0000-0000-000000000000/replay", headers=H)).status_code == 404


async def test_replay_of_unregistered_flow_is_409(dash):
    c, store = dash
    rid = await store.create("borrado", {})
    await store.finish(rid, "failed")
    assert (await c.post(f"/v1/runs/{rid}/replay", headers=H)).status_code == 409
```

Run: `uv run pytest tests/test_dashboard_api.py -v` — Expected: FAIL (404 en `/v1/flows`).

- [ ] **Step 2: Implementación**

```python
# app/api/dashboard.py
"""Endpoints que alimentan el dashboard /ui: registro con stats, listado de
runs, resumen de costes y relanzar un run."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from ..clients import Clients
from ..runs import RunStore
from .deps import require_api_key, run_store
from .flow_routes import RunAccepted, launch_run
from .routes import clients_dep

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)], tags=["Dashboard"])

EMPTY_STATS = {"runs": 0, "succeeded": 0, "cost_usd": 0.0, "last_run_at": None, "last_status": None}


@router.get("/flows", summary="Registered flows with 7-day stats")
async def list_flows(request: Request, store: RunStore = Depends(run_store)) -> dict:
    stats = await store.flow_stats(days=7)
    flows = [
        {
            "slug": spec.slug,
            "title": spec.title,
            "mode": spec.mode,
            "replaces": spec.replaces,
            "endpoint": f"/v1/flows/{spec.slug}",
            "stats": stats.get(spec.slug, EMPTY_STATS),
        }
        for spec in request.app.state.flow_registry.values()
    ]
    return {"flows": sorted(flows, key=lambda f: f["slug"])}


@router.get("/runs", summary="List runs, newest first")
async def list_runs(
    flow: str | None = None,
    status: str | None = Query(None, pattern="^(running|succeeded|failed)$"),
    since: str | None = None,
    until: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    store: RunStore = Depends(run_store),
) -> dict:
    return await store.list_runs(
        flow=flow, status=status, since=since, until=until, page=page, per_page=per_page
    )


@router.get("/costs/summary", summary="Cost totals by day or provider")
async def costs_summary(
    group_by: Literal["day", "provider"] = "day",
    days: int = Query(30, ge=1, le=365),
    store: RunStore = Depends(run_store),
) -> dict:
    return {"items": await store.costs_summary(group_by=group_by, days=days)}


@router.post(
    "/runs/{run_id}/replay",
    status_code=202,
    response_model=RunAccepted,
    summary="Re-run a flow with the stored input of a previous run",
    responses={
        404: {"description": "Run not found"},
        409: {"description": "The run's flow is no longer registered"},
        422: {"description": "Stored input no longer matches the flow's input model"},
    },
)
async def replay_run(
    run_id: str,
    request: Request,
    store: RunStore = Depends(run_store),
    clients: Clients = Depends(clients_dep),
) -> RunAccepted:
    run = await store.get(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found")
    spec = request.app.state.flow_registry.get(run["flow"])
    if spec is None:
        raise HTTPException(409, detail=f"Flow '{run['flow']}' is no longer registered")
    try:
        data = spec.input_model.model_validate(run["input"])
    except ValidationError as exc:
        raise HTTPException(422, detail="Stored input no longer matches the flow") from exc
    return await launch_run(
        spec, data, request=request, store=store, clients=clients, replay_of=run_id
    )
```

En `app/main.py`: `from .api.dashboard import router as dashboard_router`, `app.include_router(dashboard_router)` tras los otros, y añadir `{"name": "Dashboard", "description": "Data behind /ui."}` a `openapi_tags`.

- [ ] **Step 3: Verificar y commitear**

Run: `uv run pytest -v && uv run ruff check .`

```bash
git add app/api/dashboard.py app/main.py tests/test_dashboard_api.py
git commit -m "feat: API del dashboard (flows, runs, costes, replay)"
```

---

### Task B5: Logging JSON con `run_id`

**Files:**
- Create: `app/obslog.py`
- Modify: `app/config.py` (campo `log_format: str = "text"`), `app/main.py` (usar `setup_logging`), `.env.example` (`LOG_FORMAT=`)
- Test: `tests/test_obslog.py`

**Interfaces:**
- Consumes: `current_run` de `app/runs.py`.
- Produces: `setup_logging(fmt: str)`; con `LOG_FORMAT=json` cada línea es un JSON con `ts, level, logger, message` y `run_id` si hay run en contexto. Default `text` (formato actual).

- [ ] **Step 1: Test que falla**

```python
# tests/test_obslog.py
from __future__ import annotations

import json
import logging

from app.obslog import JsonFormatter
from app.runs import MemoryRunStore, bind_run


def _record(msg: str = "hola %s", args: tuple = ("mundo",)) -> logging.LogRecord:
    return logging.LogRecord("app.test", logging.INFO, __file__, 1, msg, args, None)


def test_json_formatter_without_run():
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["message"] == "hola mundo" and payload["level"] == "INFO"
    assert "run_id" not in payload


async def test_json_formatter_includes_run_id():
    store = MemoryRunStore()
    run_id = await store.create("echo", {})
    with bind_run(store, run_id):
        payload = json.loads(JsonFormatter().format(_record()))
    assert payload["run_id"] == run_id
```

- [ ] **Step 2: Implementación**

```python
# app/obslog.py
"""Logging estructurado: con LOG_FORMAT=json cada línea sale como JSON con el
run_id del flujo en curso, para correlacionar los logs de Railway con un run."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .runs import current_run

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        ctx = current_run.get()
        if ctx is not None:
            payload["run_id"] = ctx.run_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(fmt: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else logging.Formatter(TEXT_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
```

En `app/config.py`, junto a los demás campos de `Settings`: `log_format: str = "text"`.
En `app/main.py`, sustituir la línea `logging.basicConfig(...)` del final por:

```python
from .obslog import setup_logging

setup_logging(get_settings().log_format)
app = create_app()
```

En `.env.example` añadir `LOG_FORMAT=` con comentario `# json en Railway, text en local`.

- [ ] **Step 3: Verificar y commitear**

Run: `uv run pytest -v && uv run ruff check .`

```bash
git add app/obslog.py app/config.py app/main.py .env.example tests/test_obslog.py
git commit -m "feat: logging JSON opcional con run_id para correlacionar con Railway"
```

---

# Track C — Dashboard web

### Task C1: UI estática en `/ui`

**Files:**
- Create: `app/ui/static/index.html`
- Modify: `app/main.py` (mount de StaticFiles)
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: contratos JSON de B4 (documentados en su bloque Interfaces; la UI puede escribirse en paralelo contra ese contrato). Nota de diseño: el spec decía "htmx"; como la API es JSON, la UI usa fetch + JS vanilla, que cumple el mismo objetivo (sin build step, sin CDN).
- Produces: SPA de un archivo con vistas Flujos / Ejecuciones / Detalle de run / Costes, API key en `localStorage` enviada como `X-API-Key`.

- [ ] **Step 1: Test que falla**

```python
# tests/test_ui.py
async def test_ui_shell_is_public(api):
    r = await api.get("/ui/")
    assert r.status_code == 200
    assert "Handoff Flows" in r.text
```

(usa la fixture `api` de `tests/test_api.py`; moverla a `tests/conftest.py` si no está ya ahí para poder reutilizarla.)

- [ ] **Step 2: Mount en `main.py`**

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles

UI_DIR = Path(__file__).resolve().parent / "ui" / "static"
```

y dentro de `create_app`, tras `include_router`:

```python
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")
```

- [ ] **Step 3: `index.html` completo**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Handoff Flows</title>
<style>
:root { --bg:#fff; --fg:#1a1a1a; --muted:#667; --line:#e2e4ea; --card:#f6f7f9;
        --ok:#157f3d; --bad:#c0392b; --run:#b7791f; --accent:#2457d6; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111418; --fg:#e8eaed; --muted:#99a; --line:#2a2f37; --card:#1a1f26;
          --ok:#4cc38a; --bad:#ff7b6b; --run:#e2b93d; --accent:#7aa2ff; }
}
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.5 ui-sans-serif, system-ui, sans-serif }
header { display:flex; gap:1rem; align-items:center; padding:.8rem 1rem;
  border-bottom:1px solid var(--line); flex-wrap:wrap }
header h1 { font-size:1.05rem; margin:0 }
nav a { color:var(--muted); text-decoration:none; padding:.25rem .6rem; border-radius:6px }
nav a.active { color:var(--fg); background:var(--card) }
#key-btn { margin-left:auto; font-size:.8rem }
main { padding:1rem; max-width:1100px; margin:0 auto }
table { width:100%; border-collapse:collapse; margin:.8rem 0 }
th, td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
  vertical-align:top; font-size:.9rem }
th { color:var(--muted); font-weight:600 }
tr.link { cursor:pointer } tr.link:hover { background:var(--card) }
.status { font-weight:600 } .status.succeeded { color:var(--ok) }
.status.failed { color:var(--bad) } .status.running { color:var(--run) }
.pill { background:var(--card); border-radius:999px; padding:.1rem .55rem; font-size:.78rem }
button, select, input { font:inherit; padding:.35rem .7rem; border-radius:6px;
  border:1px solid var(--line); background:var(--card); color:var(--fg) }
button.primary { background:var(--accent); color:#fff; border-color:var(--accent); cursor:pointer }
pre { background:var(--card); padding:.7rem; border-radius:8px; overflow:auto;
  font-size:.8rem; max-height:22rem }
.filters { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center }
.muted { color:var(--muted) } .err { color:var(--bad) }
.step { border-left:3px solid var(--line); padding:.2rem 0 .2rem .8rem; margin:.3rem 0 }
.step.failed { border-color:var(--bad) } .step.succeeded { border-color:var(--ok) }
a { color:var(--accent) }
</style>
</head>
<body>
<header>
  <h1>Handoff Flows</h1>
  <nav>
    <a href="#/flows" data-tab="flows">Flows</a>
    <a href="#/runs" data-tab="runs">Runs</a>
    <a href="#/costs" data-tab="costs">Costs</a>
  </nav>
  <button id="key-btn"></button>
</header>
<main id="view">Loading…</main>
<script>
const view = document.getElementById('view');
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = v => '$' + Number(v || 0).toFixed(4);
const when = iso => iso ? new Date(iso).toLocaleString() : '—';

function getKey() { try { return localStorage.getItem('apiKey') || ''; } catch { return ''; } }
function askKey(msg) {
  const k = prompt(msg || 'API key (sent as X-API-Key):', getKey());
  if (k !== null) { try { localStorage.setItem('apiKey', k.trim()); } catch {} render(); }
}
async function api(path, opts = {}) {
  if (!getKey()) { askKey(); if (!getKey()) throw new Error('API key required'); }
  const r = await fetch(path, { ...opts, headers: { 'X-API-Key': getKey(), ...(opts.headers || {}) } });
  if (r.status === 401) { askKey('Invalid key. API key:'); throw new Error('unauthorized'); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

const statusCell = s => `<span class="status ${esc(s)}">${esc(s)}</span>`;

async function renderFlows() {
  const { flows } = await api('/v1/flows');
  view.innerHTML = `<h2>Flows <span class="muted">(${flows.length})</span></h2>
  <table><tr><th>Flow</th><th>Mode</th><th>Replaces (n8n)</th><th>Last run</th>
  <th>Runs 7d</th><th>OK 7d</th><th>Cost 7d</th></tr>` + flows.map(f => `
  <tr class="link" onclick="location.hash='#/runs?flow=${encodeURIComponent(f.slug)}'">
    <td><strong>${esc(f.title)}</strong><br><span class="muted">${esc(f.endpoint)}</span></td>
    <td><span class="pill">${esc(f.mode)}</span></td>
    <td class="muted">${esc(f.replaces)}</td>
    <td>${f.stats.last_status ? statusCell(f.stats.last_status) + '<br>' : ''}
        <span class="muted">${when(f.stats.last_run_at)}</span></td>
    <td>${f.stats.runs}</td><td>${f.stats.succeeded}</td><td>${money(f.stats.cost_usd)}</td>
  </tr>`).join('') + '</table>';
}

async function renderRuns(params) {
  const flow = params.get('flow') || '', status = params.get('status') || '';
  const page = Number(params.get('page') || 1);
  const q = new URLSearchParams({ page, per_page: 50 });
  if (flow) q.set('flow', flow); if (status) q.set('status', status);
  const data = await api('/v1/runs?' + q);
  const pages = Math.max(1, Math.ceil(data.total / data.per_page));
  const nav = (p, label) => `<button onclick="location.hash='#/runs?${
    new URLSearchParams({ ...(flow && { flow }), ...(status && { status }), page: p })}'">${label}</button>`;
  view.innerHTML = `<h2>Runs <span class="muted">(${data.total})</span></h2>
  <div class="filters">
    <input id="f-flow" placeholder="flow slug" value="${esc(flow)}">
    <select id="f-status">
      ${['', 'running', 'succeeded', 'failed'].map(s =>
        `<option value="${s}" ${s === status ? 'selected' : ''}>${s || 'any status'}</option>`).join('')}
    </select>
    <button class="primary" id="f-go">Filter</button>
    <span class="muted">page ${data.page}/${pages}</span>
    ${data.page > 1 ? nav(data.page - 1, '←') : ''} ${data.page < pages ? nav(data.page + 1, '→') : ''}
  </div>
  <table><tr><th>When</th><th>Flow</th><th>Status</th><th>Error</th></tr>` +
  data.items.map(r => `
  <tr class="link" onclick="location.hash='#/runs/${r.id}'">
    <td>${when(r.created_at)}${r.replay_of ? ' <span class="pill">replay</span>' : ''}${
      r.dry_run ? ' <span class="pill">dry run</span>' : ''}</td>
    <td>${esc(r.flow)}</td><td>${statusCell(r.status)}</td>
    <td class="err">${esc((r.error || '').slice(0, 90))}</td>
  </tr>`).join('') + '</table>';
  document.getElementById('f-go').onclick = () => {
    const q = new URLSearchParams();
    const f = document.getElementById('f-flow').value.trim();
    const s = document.getElementById('f-status').value;
    if (f) q.set('flow', f); if (s) q.set('status', s);
    location.hash = '#/runs?' + q;
  };
}

async function renderRun(id) {
  const run = await api('/v1/runs/' + id);
  view.innerHTML = `<p><a href="#/runs">← runs</a></p>
  <h2>${esc(run.flow)} ${statusCell(run.status)}</h2>
  <p class="muted">run ${esc(run.id)} · ${when(run.created_at)} → ${when(run.finished_at)}
    · cost ${money(run.total_cost_usd)}
    ${run.replay_of ? `· replay of <a href="#/runs/${esc(run.replay_of)}">original</a>` : ''}
    ${run.dry_run ? '· <span class="pill">dry run</span>' : ''}</p>
  ${run.error ? `<p class="err">${esc(run.error)}</p>` : ''}
  <button class="primary" id="replay">Replay with same input</button>
  <h3>Steps</h3>
  ${(run.steps || []).map(s => `<div class="step ${esc(s.status)}">
      <strong>${esc(s.name)}</strong> <span class="muted">${esc(s.status)} · ${when(s.at)}</span>
      ${s.detail ? `<pre>${esc(JSON.stringify(s.detail, null, 2))}</pre>` : ''}</div>`).join('')
    || '<p class="muted">No steps recorded.</p>'}
  <h3>Costs</h3>
  ${(run.costs || []).length ? '<table><tr><th>Provider</th><th>Operation</th><th>Units</th><th>USD</th></tr>' +
    run.costs.map(c => `<tr><td>${esc(c.provider)}</td><td>${esc(c.operation)}</td>
      <td>${c.units}</td><td>${money(c.cost_usd)}</td></tr>`).join('') + '</table>'
    : '<p class="muted">No paid calls.</p>'}
  <h3>Input</h3><pre>${esc(JSON.stringify(run.input, null, 2))}</pre>
  <h3>Output</h3><pre>${esc(JSON.stringify(run.output, null, 2))}</pre>`;
  document.getElementById('replay').onclick = async () => {
    if (!confirm('Re-run this flow with the same input?')) return;
    try {
      const r = await api(`/v1/runs/${id}/replay`, { method: 'POST' });
      location.hash = '#/runs/' + r.run_id;
    } catch (e) { alert('Replay failed: ' + e.message); }
  };
}

async function renderCosts() {
  const [byDay, byProvider] = await Promise.all([
    api('/v1/costs/summary?group_by=day&days=30'),
    api('/v1/costs/summary?group_by=provider&days=30'),
  ]);
  const table = (items, col) => items.length
    ? `<table><tr><th>${col}</th><th>USD</th></tr>` + items.map(i =>
        `<tr><td>${esc(i[col.toLowerCase()])}</td><td>${money(i.cost_usd)}</td></tr>`).join('') + '</table>'
    : '<p class="muted">No costs in the last 30 days.</p>';
  view.innerHTML = `<h2>Costs — last 30 days</h2>
    <h3>By provider</h3>${table(byProvider.items, 'Provider')}
    <h3>By day</h3>${table(byDay.items, 'Day')}`;
}

async function render() {
  const hash = location.hash || '#/flows';
  const [path, query] = hash.slice(2).split('?');
  const params = new URLSearchParams(query || '');
  const tab = path.split('/')[0] || 'flows';
  document.querySelectorAll('nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.tab === tab));
  document.getElementById('key-btn').textContent = getKey() ? 'API key ✓' : 'Set API key';
  view.innerHTML = '<p class="muted">Loading…</p>';
  try {
    if (path.startsWith('runs/')) await renderRun(path.split('/')[1]);
    else if (tab === 'runs') await renderRuns(params);
    else if (tab === 'costs') await renderCosts();
    else await renderFlows();
  } catch (e) {
    view.innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}
document.getElementById('key-btn').onclick = () => askKey();
window.addEventListener('hashchange', render);
render();
</script>
</body>
</html>
```

- [ ] **Step 4: Verificar**

Run: `uv run pytest -v && uv run ruff check .`
Y a mano: `uv run uvicorn app.main:app --reload`, abrir `http://localhost:8000/ui/`, meter la API key, recorrer Flows → Runs → detalle → Costs y probar Replay sobre un run.

- [ ] **Step 5: Commit**

```bash
git add app/ui app/main.py tests/test_ui.py
git commit -m "feat: dashboard /ui con flujos, ejecuciones, costes y relanzar"
```

---

# Track D — Scaffold y documentación

### Task D1: `scripts/new_flow.py`

**Files:**
- Create: `scripts/__init__.py` (vacío), `scripts/new_flow.py`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: convención de A1 (solo nombres; no importa el registro).
- Produces: `create_flow(slug, *, title=None, replaces="", root=Path(".")) -> list[Path]`; CLI `uv run python scripts/new_flow.py <slug> [--replaces "..."]`.

- [ ] **Step 1: Test que falla**

```python
# tests/test_scaffold.py
from __future__ import annotations

import pytest

from scripts.new_flow import create_flow


def test_scaffold_creates_flow_package(tmp_path):
    created = create_flow("lead-scoring", replaces="Lead Scoring v2", root=tmp_path)
    pkg = tmp_path / "app" / "flows" / "lead_scoring"
    assert {pkg / "flow.py", pkg / "schemas.py", pkg / "__init__.py", pkg / "README.md",
            tmp_path / "tests" / "flows" / "test_lead_scoring.py"} == set(created)
    flow = (pkg / "flow.py").read_text()
    assert 'slug="lead-scoring"' in flow and "Lead Scoring v2" in flow
    assert "LeadScoringInput" in (pkg / "schemas.py").read_text()


def test_scaffold_rejects_bad_slug_and_duplicates(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        create_flow("Bad Slug", root=tmp_path)
    create_flow("dup", root=tmp_path)
    with pytest.raises(FileExistsError):
        create_flow("dup", root=tmp_path)
```

- [ ] **Step 2: Implementación**

```python
#!/usr/bin/env python3
# scripts/new_flow.py
"""Genera el esqueleto de un flow nuevo según la convención del repo.

Uso: uv run python scripts/new_flow.py lead-scoring --replaces "Lead Scoring v2"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

FLOW_PY = '''"""{title}: TODO qué hace, en una línea.

Sustituye al workflow de n8n «{replaces}».
"""

from __future__ import annotations

import logging

from app.clients import Clients
from app.flows.registry import FlowSpec
from app.runs import record_step

from .schemas import {cls}Input, {cls}Result

logger = logging.getLogger(__name__)


async def run(clients: Clients, data: {cls}Input) -> {cls}Result:
    await record_step("todo", "running", {{"hint": "sustituir por los pasos reales"}})
    raise NotImplementedError("TODO: implementar {slug}")


spec = FlowSpec(
    slug="{slug}",
    title="{title}",
    input_model={cls}Input,
    response_model={cls}Result,
    run=run,
    mode="sync",  # "async" si debe responder 202 + run_id
    replaces="{replaces}",
    summary="TODO: one line for Swagger",
)
'''

SCHEMAS_PY = '''from __future__ import annotations

from pydantic import BaseModel


class {cls}Input(BaseModel):
    pass  # TODO: campos del trigger de n8n


class {cls}Result(BaseModel):
    pass  # TODO: lo que devuelve el flow
'''

INIT_PY = """from .flow import run, spec
from .schemas import {cls}Input, {cls}Result

__all__ = ["{cls}Input", "{cls}Result", "run", "spec"]
"""

README = """# {title}

Sustituye al workflow de n8n **{replaces}**.

## Qué hace

TODO: 3-5 líneas.

## Qué reusa

- clients: TODO
- flows/domain: TODO

## Qué verificar con credenciales reales

TODO.
"""

TEST_PY = '''from app.flows.{pkg} import spec
from app.flows.registry import discover


def test_spec_registrado():
    assert discover()["{slug}"] is spec

# TODO: tests del flow con HTTP simulado (respx), como tests/test_flows.py.
'''


def create_flow(
    slug: str, *, title: str | None = None, replaces: str = "", root: Path = Path(".")
) -> list[Path]:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"slug inválido {slug!r} (kebab-case: a-z, 0-9, '-')")
    pkg = slug.replace("-", "_")
    cls = "".join(p.capitalize() for p in slug.split("-"))
    title = title or " ".join(p.capitalize() for p in slug.split("-"))
    pkg_dir = root / "app" / "flows" / pkg
    if pkg_dir.exists():
        raise FileExistsError(f"{pkg_dir} ya existe")
    pkg_dir.mkdir(parents=True)
    tests_dir = root / "tests" / "flows"
    tests_dir.mkdir(parents=True, exist_ok=True)
    ctx = {"slug": slug, "pkg": pkg, "cls": cls, "title": title, "replaces": replaces or "TODO"}
    files = {
        pkg_dir / "flow.py": FLOW_PY,
        pkg_dir / "schemas.py": SCHEMAS_PY,
        pkg_dir / "__init__.py": INIT_PY,
        pkg_dir / "README.md": README,
        tests_dir / f"test_{pkg}.py": TEST_PY,
    }
    for path, template in files.items():
        path.write_text(template.format(**ctx))
    return list(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="kebab-case, p. ej. lead-scoring")
    parser.add_argument("--title", default=None)
    parser.add_argument("--replaces", default="", help="nombre del workflow de n8n")
    args = parser.parse_args()
    for path in create_flow(args.slug, title=args.title, replaces=args.replaces):
        print(f"  creado {path}")
    print("\nSiguiente: rellenar schemas.py, flow.py y el test. Ver CLAUDE.md.")


if __name__ == "__main__":
    main()
```

Nota: `tests/flows/` necesita existir como paquete de pytest normal (sin `__init__.py` está bien con rootdir actual).

- [ ] **Step 3: Verificar y commitear**

Run: `uv run pytest tests/test_scaffold.py -v && uv run ruff check .`

```bash
git add scripts tests/test_scaffold.py
git commit -m "feat: scaffold de flows nuevos (scripts/new_flow.py)"
```

---

### Task D2: `CLAUDE.md` y README

**Files:**
- Create: `CLAUDE.md`
- Modify: `README.md` (secciones de convención, dashboard y scaffold)

**Interfaces:**
- Consumes: todo lo anterior terminado (es la foto final).
- Produces: el "contexto pequeño" que un agente necesita para migrar un workflow sin leer el resto del sistema.

- [ ] **Step 1: Escribir `CLAUDE.md`**

```markdown
# handoff-flows — guía para agentes

Servicio FastAPI donde viven los workflows migrados de n8n. Monolito modular:
cada flow es un paquete que se registra solo.

## Convención de un flow

    app/flows/<pkg>/
      flow.py      # `spec` (FlowSpec) + `async def run(clients, data)`
      schemas.py   # Pydantic de entrada/salida
      __init__.py  # re-exporta spec, run y schemas
      README.md    # qué hace, qué reusa, qué workflow de n8n sustituye
    tests/flows/test_<pkg>.py

- El registro (`app/flows/registry.py`) descubre el paquete y lo monta como
  `POST /v1/flows/<slug>` con run, steps, coste y dry_run automáticos.
- `mode="sync"` responde directo; `mode="async"` responde 202 + run_id.
- Un paquete puede exportar `specs` (tupla) si varios workflows pequeños
  comparten implementación (ver `app/flows/crm/`).
- Imports absolutos (`from app.clients import ...`) dentro de los paquetes.

## Reuso (en este orden)

1. `app/clients/` — un archivo por API externa. NO dupliques clients: si el
   proveedor ya existe, úsalo; si es nuevo, créalo siguiendo `clients/base.py`
   y registra su coste con `record_cost`.
2. `app/domain/` — lógica pura sin I/O.
3. Otros flows — se llaman como funciones Python (`from app.flows.company
   import enrich_company`), nunca por HTTP interno. Sus steps y costes caen
   en el run del padre automáticamente (ContextVar).

## Migrar un workflow de n8n

1. Exportar el JSON del workflow desde n8n.
2. `uv run python scripts/new_flow.py <slug> --replaces "<nombre en n8n>"`.
3. Implementar `schemas.py` (el input es el cuerpo del trigger de n8n) y
   `flow.py`, marcando pasos con `record_step`. Mirar un flow parecido.
4. Tests con HTTP simulado (respx), como `tests/test_flows.py`. Sin red.
5. `uv run pytest && uv run ruff check .` en verde.
6. Deploy; probar con `dry_run` si aplica; revisar el run en `/ui`.
7. En n8n: sustituir el workflow por un HTTP Request a
   `POST /v1/flows/<slug>` con `X-API-Key`. Tras unos días estable, apagarlo.

## Reglas del repo

- Copy de API y Swagger en inglés; código y comentarios en español.
- Sin dependencias nuevas sin discutirlo.
- Toda llamada de pago registra coste (`record_cost`); todo paso relevante,
  `record_step`.
- Config por variables de entorno (`app/config.py` + `.env.example`).
- Dashboard en `/ui`; API de observabilidad en `GET /v1/flows`, `GET /v1/runs`,
  `GET /v1/costs/summary`, `POST /v1/runs/{id}/replay`.
```

- [ ] **Step 2: Actualizar `README.md`**

Añadir tras la sección "Endpoints":

```markdown
## Dashboard

`/ui` (misma API key): flujos registrados con stats de 7 días, ejecuciones con
steps y costes, resumen de costes, y relanzar un run con su mismo input.

## Añadir un flow nuevo

```bash
uv run python scripts/new_flow.py mi-flujo --replaces "Mi Flujo (n8n)"
```

Crea `app/flows/mi_flujo/` siguiendo la convención (ver `CLAUDE.md`); al
arrancar aparece solo en Swagger, en `/ui` y en `GET /v1/flows`.
```

y en la tabla de endpoints, añadir las filas de `GET /v1/flows`, `GET /v1/runs`,
`GET /v1/costs/summary` y `POST /v1/runs/{run_id}/replay`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: convención de flows, dashboard y proceso de migración"
```

---

## Self-review del plan (hecho)

- **Cobertura del spec:** registro+FlowSpec (A1–A2), convención+paquetes (A3–A5), dashboard lectura+relanzar (B4+C1), logs estructurados (B5), replay_of (B1), listados/stats (B2–B3), scaffold+contexto pequeño (D1–D2), proceso de los 26 (CLAUDE.md §Migrar). La migración de los 26 workflows en sí es la fase 3 del spec y queda fuera de este plan: usa el proceso de CLAUDE.md, un plan corto por lote.
- **Tipos consistentes:** `create(..., replay_of)` (B1) ↔ `launch_run` (A2, kwargs condicional); `flow_stats` keys ↔ `EMPTY_STATS` (B4) ↔ UI (`stats.runs`, `stats.cost_usd`); items de `list_runs` ↔ columnas de la tabla Runs de la UI.
- **Sin placeholders** en pasos de código; los TODO que aparecen son contenido generado por el scaffold (intencional).
