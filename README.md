# handoff-flows

Los flujos de n8n de **Discovery Call Booked** migrados a código: un servicio
FastAPI en el que el flujo completo y cada subflujo son endpoints propios.
Swagger en `/docs`, ReDoc en `/redoc`.

- Diseño y decisiones: [docs/design.md](docs/design.md)
- Plan: [docs/plan.md](docs/plan.md)

Repo: [davideliaspalacio/n8n-yourhandoff](https://github.com/davideliaspalacio/n8n-yourhandoff).
Railway despliega `main` automáticamente (proyecto `handoff-flows` de Jake's
Projects).

## Arrancar en local

```bash
uv sync
cp .env.example .env        # como mínimo API_KEY; el resto según lo que se pruebe
uv run uvicorn app.main:app --reload
open http://localhost:8000/docs
```

Sin `DATABASE_URL` los runs viven en memoria. Con ella, el servicio aplica
`migrations/*.sql` (idempotentes) al arrancar.

```bash
uv run pytest                                  # 41 tests, sin red
TEST_DATABASE_URL=postgresql://... uv run pytest tests/test_postgres_store.py
uv run ruff check .
```

## Endpoints

Todo `/v1` pide la cabecera `X-API-Key` y devuelve `X-Run-Id`.

| Endpoint | Sustituye a | Modo |
|---|---|---|
| `POST /v1/flows/discovery-call-booked` | Discovery Call Booked | 202 + `run_id` |
| `GET /v1/runs/{run_id}` | execution-logger | pasos, salida, coste USD |
| `POST /v1/communications/search` | Search Team Communications (+ Fireflies) | síncrono |
| `POST /v1/contacts/enrich` | Enrich External Attendees | síncrono |
| `POST /v1/companies/enrich` | Enrich Company (+ Get LinkedIn Data, News + Job Search) | síncrono |
| `POST /v1/crm/companies` | Company CRM Add/Update | síncrono |
| `POST /v1/crm/contacts` | Contact CRM Add/Update | síncrono |
| `POST /v1/crm/notes` | Post Note to Hubspot | síncrono |
| `GET /v1/flows` | — | síncrono |
| `GET /v1/runs` | — | síncrono |
| `GET /v1/costs/summary` | — | síncrono |
| `POST /v1/runs/{run_id}/replay` | — | 202 + `run_id` |

El cuerpo del orquestador es el mismo que recibía el trigger de n8n
(`companyName`, `primaryContactEmail`, `guestEmails`…). Con `"dry_run": true`
investiga todo pero no llama a la edge function, ni a HubSpot, ni a Slack, y
devuelve en el run los dos mensajes que habría publicado.

`/v1/companies/enrich` responde con la forma del webhook viejo (`Company`,
`linkedIn`, `crm`), así que el Founders Club Agent puede apuntar
`ENRICHMENT_WEBHOOK_URL` aquí sin cambiar su parser. Ojo: ese cliente tendría
que mandar también `X-API-Key`.

## Dashboard

`/ui` (misma API key): flujos registrados con stats de 7 días, ejecuciones con
steps y costes, resumen de costes, y relanzar un run con su mismo input.

## Añadir un flow nuevo

```bash
uv run python scripts/new_flow.py mi-flujo --replaces "Mi Flujo (n8n)"
```

Crea `app/flows/mi_flujo/` siguiendo la convención (ver `CLAUDE.md`); al
arrancar aparece solo en Swagger, en `/ui` y en `GET /v1/flows`.

## Cortar n8n

1. Desplegar en Railway (`railway.json` + `Dockerfile`, healthcheck en `/health`)
   con las variables de `.env.example`. Desplegado en Railway: proyecto
   `handoff-flows` de Jake's Projects, con su propio Postgres.
2. Probar con `dry_run: true` y un booking real; revisar `GET /v1/runs/{id}`.
3. En el workflow que hoy hace *Execute Workflow → Discovery Call Booked*,
   cambiar ese nodo por un *HTTP Request* `POST /v1/flows/discovery-call-booked`
   con `X-API-Key`. Es el único cambio en n8n.
4. Cuando lleve unos días estable, desactivar los subflujos de n8n.

## Qué hay que verificar con credenciales reales

Los tests simulan todo el HTTP. Estas piezas se escribieron contra la
documentación de cada proveedor, sin llamadas reales, y conviene confirmarlas
en el primer `dry_run`:

- **Get LinkedIn Data / News + Job Search**: no existían como JSON; se
  reescribieron con Apollo `organizations/enrich` y SerpApi (`google_news`,
  `google_jobs`). Si el flujo viejo usaba otros proveedores (p. ej. Serper), solo
  cambia `app/clients/serpapi.py`.
- **Fireflies**: la consulta usa `transcripts(participants: [...])`
  (`app/clients/fireflies.py`).
- **HubSpot**: las propiedades personalizadas (`ai_summary`, `linkedin_uid`,
  `twitter_url`, `crunchbase_url`, `headcount_growth_*`, `ai_contact_summary`)
  tienen que existir en el portal. La URL de LinkedIn del contacto va a
  `HUBSPOT_CONTACT_LINKEDIN_PROPERTY` (por defecto `hs_linkedin_url`).
- **Edge function**: nombre y valor de la cabecera de auth de la credencial
  "Supabase Handoff Portal" (`EVENT_FUNCTION_AUTH_HEADER` / `_VALUE`).
- **Precios** de Apollo y SerpApi: están a 0 hasta que se pongan los del plan.

## Diferencias con n8n (a propósito)

- El resumen de Gmail llega a Slack. En n8n, `Set Output` devolvía la clave
  literal `'Communications.summary'` y Slack siempre decía "No prior
  communications found." justo cuando sí había correos.
- Cada bio va con su contacto: n8n las cruzaba por índice con respuestas de
  Apollo que incluían las fallidas.
- Los asistentes que Apollo no encuentra también se crean en HubSpot, con el
  nombre del booking. Antes se perdían.
- Nada revienta con `growthMetrics` vacío; `aiSummary` ya no llega a HubSpot
  entre comillas; la industria se lee bien (`CompanyIndustry` tenía una C
  mayúscula de más); `companyName: "N/A"` no se usa como nombre.
- No se envían campos vacíos a HubSpot, así que un update no borra datos
  metidos a mano.
- Fireflies se consulta aunque no haya correos.
- Comunicaciones, contactos y empresa corren en paralelo.
- Nadie de `@yourhandoff.com` se enriquece ni se crea como contacto.
- Cada llamada de pago queda con su coste en `cost_events`.
