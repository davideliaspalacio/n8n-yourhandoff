# handoff-flows — plan de implementación

> Ejecución inline (David pidió que se tomen todas las decisiones y se termine).
> Diseño: [design.md](design.md).

**Goal:** servicio FastAPI que reemplaza el flujo Discovery Call Booked de n8n y
expone cada subflujo como endpoint.

**Architecture:** `clients/` (HTTP fino por proveedor) → `domain/` (lógica pura
portada del JS) → `flows/` (orquestación async con modelos Pydantic) → `api/`
(rutas FastAPI). `runs.py` guarda runs y costes.

**Tech Stack:** Python 3.13, uv, FastAPI, Pydantic v2, pydantic-settings, httpx,
psycopg 3, openai, pytest, respx.

## Global Constraints

- Nada de identificadores de cuenta en el código: todo por entorno.
- Texto de Slack y Swagger en inglés; código y comentarios en español.
- Modelo por defecto `gpt-4.1`, JSON mode.
- Toda llamada de pago registra un `cost_event`.

---

### Task 1: Esqueleto, config, auth, `/health`
Files: `pyproject.toml`, `app/config.py`, `app/main.py`, `app/api/deps.py`, `tests/test_api_basics.py`.
Produces: `Settings`, `get_settings()`, `require_api_key` dependency, `create_app()`.
Tests: `/health` 200 sin clave; `/v1/*` 401 sin clave o con clave mala; 401 si `API_KEY` vacía.

### Task 2: Runs y costes
Files: `app/runs.py`, `migrations/001_flow_runs.sql`, `tests/test_runs.py`.
Produces: `RunStore` (protocolo) con `create(flow, input) -> str`, `step(run_id, name, status, output|error)`,
`finish(run_id, status, output|error)`, `get(run_id)`, `add_cost(run_id, provider, operation, units, cost_usd, meta)`,
`fail_orphans()`; `MemoryRunStore`, `PostgresRunStore`; contexto `cost_context(run_id)` con `contextvars`.

### Task 3: Dominio puro
Files: `app/domain/{emails,industry,summaries,slack_messages,naming}.py` + tests con los pinData.
Produces: `validate_emails`, `split_email_string`, `clean_messages(raw_items) -> list[CleanEmail]`,
`to_hubspot_industry(raw) -> str|None`, `extract_summary(content) -> str`,
`build_messages(...) -> SlackMessages`, `company_name_from_url`, `domain_of`.

### Task 4: Clientes HTTP
Files: `app/clients/{http,openai_llm,apollo,unipile,fireflies,serpapi,hubspot,slack,alma,event_function}.py`, tests con respx.
Produces: `request_json(...)` con reintentos 429/5xx; `LLM.complete_json(prompt, stage) -> LLMResult`; un cliente por proveedor.

### Task 5: Flujos
Files: `app/flows/*.py` + tests.
Produces: `search_team_communications`, `enrich_contacts`, `enrich_company`, `upsert_company`,
`upsert_contact`, `run_discovery_call`.

### Task 6: Rutas, Swagger, despliegue
Files: `app/api/routes.py`, `Dockerfile`, `railway.json`, `.env.example`, `README.md`.
Tests: 202 + GET run; `dry_run` no llama HubSpot ni Slack.
