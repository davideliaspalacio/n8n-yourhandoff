# enrich-contacts

Enriquece a los asistentes externos de una reunión: Apollo `people/match` y una
bio para la llamada por contacto, generada por GPT-4.1.

- Endpoint: `POST /v1/flows/enrich-contacts` (alias antiguo `/v1/contacts/enrich`).
- Entrada: `ContactsRequest` (`emails`). Salida: `list[EnrichedContact]`.
- Cada contacto se procesa por separado y en paralelo: un fallo no afecta a los
  demás y la bio no se puede pegar a otro contacto (el bug del índice en n8n).

## Qué reusa
- Clients: `apollo` (match_person), `alma` (prompt "Contact Analysis (Discovery
  Prep)"), `llm`.
- Dominio: `normalize_emails`.
- `discovery_call` importa `enrich_contacts` desde este paquete.

## Qué sustituye
El workflow de n8n "Enrich External Attendees".

## Verificar con credenciales reales
- Que Apollo encuentre a personas reales y que `company_domain_mismatch` se active
  cuando la empresa de Apollo no cuadra con el dominio del correo.
- Que un correo sin coincidencia devuelva `enriched=false` sin llamar al modelo.
