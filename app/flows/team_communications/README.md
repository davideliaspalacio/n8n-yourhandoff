# team-communications

Busca el historial de comunicaciones del equipo con unos contactos (Gmail vía
Unipile y reuniones de Fireflies) y lo resume con GPT-4.1.

- Endpoint: `POST /v1/flows/team-communications` (alias antiguo
  `/v1/communications/search`).
- Entrada: `CommunicationsRequest` (`emails`; se ignoran los internos).
  Salida: `CommunicationsResult`.
- Cambio respecto a n8n: Fireflies se consulta aunque no haya correos.

## Qué reusa
- Clients: `alma` (cuentas Google y prompt "Communications Analysis (Email)"),
  `unipile`, `fireflies`, `llm`.
- Dominio: `external_emails`, `clean_messages`, `email_context`.
- `discovery_call` importa `search_team_communications` y `NO_COMMUNICATIONS`.

## Qué sustituye
El workflow de n8n "Search Team Communications" (+ Fireflies).

## Verificar con credenciales reales
- Que Unipile devuelva correos de cada cuenta del equipo y que, si fallan todas,
  el flow responda con error en vez de un resumen vacío.
- Que Fireflies degrade bien sin clave o con error.
- Que el resumen refleje correos y reuniones reales.
