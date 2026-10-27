# discovery-call-booked

Orquestador principal: une a todos los demás flows cuando se agenda una
discovery call.

- Endpoint: `POST /v1/flows/discovery-call-booked` (asíncrono: responde 202 con
  `run_id` y `status_url`; los pasos, salida y coste se consultan en `/v1/runs/{id}`).
- Entrada: `DiscoveryCallInput` (con `dry_run`). Salida del run: `DiscoveryCallResult`.

## Flujo
edge function (fatal) -> emails de asistentes -> en paralelo: comunicaciones,
contactos y empresa (3 intentos) -> HubSpot empresa y contactos -> Slack
(anuncio + hilo). Los pasos de investigación degradan; solo la edge function y
Slack hacen fallar el run. Con `dry_run` no se escribe nada fuera.

## Qué reusa
- Flows: `attendees`, `company`, `crm`, `team_communications`.
- Clients: `event_function`, `slack`, `hubspot`.

## Qué sustituye
El workflow de n8n "Discovery Call Booked".

## Verificar con credenciales reales
- Un run con `dry_run=true` y otro real contra un evento de prueba.
- Que el nombre del run en `/v1/runs` sea ahora `discovery-call-booked`.
