# Sync HubSpot to Alma

Sustituye al workflow de n8n **Sync Hubspot to Alma** (cron cada hora al :02 y
webhook que dispara Alma; ambos ejecutaban el mismo pipeline).

## Qué hace

Busca en HubSpot los contactos modificados desde `since` (por defecto, hace 1 hora),
descarta cuentas de prueba (`EXCLUDED_EMAILS`, p. ej. `fred@fireflies.ai`), contactos
sin empresa y empresas sin nombre, y hace upsert en el Supabase de Alma de las
empresas, los contactos (por `hubspot_id`) y el vínculo `contact_company`.
Un vínculo ya existente (409) se ignora y cuenta en `links_skipped`.
Con `dry_run=true` lee todo pero no escribe: devuelve `would_upsert_*`.
Es `async` (202 + run_id): lo dispara un cron/webhook y puede tardar.

## Bugs del original que NO se replican

1. `searchSince` estaba fijo (`1792728000000`): ahora es el parámetro `since`.
2. `"after": "100"` fijo rompía la paginación: ahora se sigue `paging.next.after`.

## Qué reusa

- clients: `hubspot` (`search_contacts_modified_since`, `get_company`) y `alma`
  (`find`, `upsert`, `insert`), añadidos a los clients existentes.
- Sin coste (`record_cost`): ni HubSpot ni Supabase cobran por llamada.

## Producción y tests

El schedule se queda en n8n por ahora: pasa a ser un HTTP Request
`POST /v1/flows/sync-hubspot-to-alma` con `X-API-Key`.
Tests en `tests/flows/test_sync_hubspot_to_alma.py` (lo genera el scaffold y los
helpers de respx se definen ahí mismo, sin tocar `tests/world.py`).

## Qué verificar con credenciales reales

Un primer `dry_run=true` con `since` acotado: comprobar `would_upsert_*`, que las
tablas `companies`/`contacts`/`contact_company` tienen constraint único en
`hubspot_id` / `(contact_id, company_id)`, y que el 409 del link llega como tal.
