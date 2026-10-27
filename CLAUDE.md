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

Antes de cualquier flow, corre `uv run python scripts/catalog.py`: inventario
vivo (flows, clients, dominio, settings) para reusar en vez de duplicar.
Existe la skill `crear-flujo` (`.claude/skills/crear-flujo/SKILL.md`) con el
proceso completo y el agente `flow-builder` que la sigue; ver
`docs/ai-development.md`.

1. Exportar el JSON del workflow desde n8n.
2. `uv run python scripts/new_flow.py <slug> --replaces "<nombre en n8n>" [--kind node]`.
   `kind` del `FlowSpec` (`workflow` por defecto): `workflow` = sustituye un
   workflow completo de n8n / se dispara desde fuera; `node` = subflujo
   reutilizable que otros flows llaman como función. El dashboard los
   separa en dos tabs (Workflows / Nodes).
3. Implementar `schemas.py` (el input es el cuerpo del trigger de n8n) y
   `flow.py`, marcando pasos con `record_step`. Mirar un flow parecido.
   Rellenar `description` en el `FlowSpec` (obligatorio): párrafo de 2-4 frases
   con el contexto del flow, que se ve en el bloque "About" al entrar a /ui;
   cada nodo del `graph` lleva además `"about"` (1-2 frases: qué hace ese paso),
   que se ve al hacer clic en el nodo.
   Declarar `graph` en el `FlowSpec`: nodos `{"id", "label", "to": (ids)}`.
   Los ids deben coincidir con los nombres de `record_step` (así el último
   run colorea cada nodo); se permiten nodos solo declarativos. El dashboard
   pinta el mapa en `#/flows/<slug>` aunque el flow nunca haya corrido, y
   `validate()` rechaza al arrancar ids duplicados o destinos inexistentes.
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
