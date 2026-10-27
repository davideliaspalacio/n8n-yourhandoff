# contact-enrichment

Sustituye al workflow de n8n **Contact Enrichment Webhook**.

## Qué hace

Recibe `{email, companyHubspotId?}`, enriquece el contacto (Apollo + bio con LLM) y lo
upsertea en HubSpot, asociándolo a la empresa si se indica. Responde con el resultado
del upsert (`contact`), `enriched` y, en `dry_run`, `would_upsert`.

- Endpoint: `POST /v1/flows/contact-enrichment` (sync).
- Paridad con n8n: el email se recorta y es obligatorio; si el enriquecimiento no
  encuentra nada (`enriched=false`) igual se upsertea con los campos vacíos.
- `linkedinUid` se envía vacío (EnrichedContact no lo trae; mismo fallback que el Code de n8n).
- Mejora de la plataforma (no existía en n8n): `dry_run=true` enriquece pero no escribe en
  HubSpot y devuelve el `ContactCrmInput` que habría enviado en `would_upsert`.

## Qué reusa

Compone otros flows como funciones Python (sus steps y costes caen en este run):
- `app.flows.attendees.enrich_contacts` (sustituye "Enrich External Attendees").
- `app.flows.crm.upsert_contact` (sustituye "Contact CRM Add/Update").

Steps propios: `enrich` y `crm_upsert` (`skipped` en dry_run).

## El corte

En n8n, el webhook pasa a un HTTP Request `POST /v1/flows/contact-enrichment` con
`X-API-Key`; tras unos días estable, se apaga el workflow.

## Qué verificar con credenciales reales

Un email conocido con y sin coincidencia en Apollo, con `dry_run` primero.
