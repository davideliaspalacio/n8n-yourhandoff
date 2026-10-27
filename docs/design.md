# Migración de los flujos de n8n a `handoff-flows`

Fecha: 2026-09-30 · Estado: aprobado por David

## Objetivo

Sacar de n8n el flujo **Discovery Call Booked** y sus subflujos y dejarlos como
un servicio FastAPI reutilizable (Swagger en `/docs`), desplegable en Railway,
en el que cada subflujo es también un endpoint propio.

`handoff-flows/` es autónomo: su propio `pyproject.toml`, sin importar nada de
`handoff_agent`. Para llevarlo a su propio repo basta con copiar la carpeta.

## Flujos migrados

Desde 2026-10-26 los flujos viven como paquetes con FlowSpec (ver CLAUDE.md).

| n8n | Módulo | Endpoint |
|---|---|---|
| Discovery Call Booked | `flows/discovery_call/` | `POST /v1/flows/discovery-call-booked` (202) |
| Search Team Communications | `flows/team_communications/` | `POST /v1/communications/search` |
| Enrich External Attendees | `flows/attendees/` | `POST /v1/contacts/enrich` |
| Enrich Company (+ Get LinkedIn Data, News + Job Search) | `flows/company/` | `POST /v1/companies/enrich` |
| Company CRM Add/Update | `flows/crm/` | `POST /v1/crm/companies` |
| Contact CRM Add/Update | `flows/crm/` | `POST /v1/crm/contacts` |
| Post Note to Hubspot | `clients/hubspot.py` | `POST /v1/crm/notes` |
| execution-logger | `runs.py` | `GET /v1/runs/{run_id}` |

Los subflujos que no existían como JSON se reescriben contra las APIs, deduciendo
la forma de sus datos de lo que consumía el flujo principal:

- **Get LinkedIn Data** → Apollo `organizations/enrich` por dominio.
- **News + Job Search** → SerpApi, engines `google_news` y `google_jobs`.
- **Get Fireflies Short Summaries** → Fireflies GraphQL `transcripts(participants)`,
  3 más recientes por email, `summary.short_summary`.
- **Post Note to Hubspot** → HubSpot CRM v3 `notes` asociada a empresa (tipo 190)
  o contacto (tipo 202).
- La edge function `handle-discovery-call-booked` no es n8n: se sigue llamando por HTTP.

## Decisiones

- Orquestador asíncrono (202 + `run_id`) como tarea en proceso; al arrancar, los
  runs `running` huérfanos pasan a `failed`. Subflujos síncronos.
- Comunicaciones, contactos y empresa corren en paralelo; los contactos también.
- `flow_runs` y `cost_events` en Postgres (`DATABASE_URL`). Sin `DATABASE_URL`
  el servicio usa un almacén en memoria (local y tests).
- Prompts y cuentas del equipo se siguen leyendo del Supabase de Alma por REST,
  para que los prompts se sigan editando donde ya están.
- Autenticación por `X-API-Key` (`API_KEY`). Sin clave configurada se rechaza
  todo salvo `/health` y la documentación: fallar cerrado.
- `dry_run` en el orquestador: investiga, pero no escribe en HubSpot ni en Slack,
  y devuelve los mensajes que habría enviado.
- Todo identificador de cuenta (portal de HubSpot, canal de Slack, DSN de
  Unipile, URL de la edge function) va por variable de entorno.
- Coste en USD de cada llamada de pago (OpenAI por tokens, Apollo por crédito,
  SerpApi por búsqueda) en `cost_events`, con precios por entorno.
- Swagger y mensajes en inglés; código y comentarios en español.

## Política de errores

| Paso | Si falla |
|---|---|
| Edge function | run `failed` (sin ella no hay fecha ni zona horaria del evento) |
| Comunicaciones | "No prior communications found." |
| Un contacto | nombre y apellido del trigger para ese contacto |
| Empresa | 3 intentos; luego nombre derivado del dominio |
| HubSpot | el mensaje sale sin enlaces de HubSpot |
| Slack | run `failed` |

Los clientes HTTP reintentan 429 y 5xx con backoff exponencial (3 intentos).

## Bugs de n8n corregidos a propósito

1. `Set Output` devolvía la clave literal `'Communications.summary'`: el resumen
   de Gmail nunca llegaba a Slack justo cuando había correos.
2. Las bios se cruzaban por índice con respuestas de Apollo que incluían las
   fallidas, y el subflujo perdía una rama cuando había éxitos y fallos.
3. `growthMetrics[0].growthPercentage` reventaba con la lista vacía.
4. `aiSummary` llegaba a HubSpot entre comillas por un `JSON.stringify` de más.
5. `crm.CompanyIndustry` (C mayúscula) dejaba la industria vacía.
6. `Gather Contact Summaries` no tenía consumidor: se elimina.

## Pruebas

pytest + respx. Lógica pura (`domain/`) con los `pinData` de n8n como fixtures;
flujos con todos los HTTP simulados; API con `TestClient` de FastAPI.
