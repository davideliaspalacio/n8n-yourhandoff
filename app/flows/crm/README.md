# crm

Tres flows pequeños de HubSpot que comparten implementación (`specs = (...)`).

- `POST /v1/flows/crm-company`: crea o actualiza una empresa (por dominio) y
  publica su nota de IA. Entrada `CompanyCrmInput`, salida `CompanyCrmResult`.
- `POST /v1/flows/crm-contact`: crea o actualiza un contacto (por email) y lo
  asocia a una empresa. Entrada `ContactCrmInput`, salida `ContactCrmResult`.
- `POST /v1/flows/crm-note`: publica una nota en una empresa o contacto
  (alias antiguo `/v1/crm/notes`). Entrada `NoteInput`, salida `NoteResult`.

## Qué reusa
- Clients: `hubspot` (companies, contacts, associations, notes).
- Dominio: `to_hubspot_industry`, `domain_of`.
- `discovery_call` importa `upsert_company` y `upsert_contact` desde aquí.
- Los modelos conservan los nombres de campo de n8n.

## Qué sustituye
Los workflows de n8n "Company CRM Add/Update", "Contact CRM Add/Update" y
"Post Note to Hubspot".

## Verificar con credenciales reales
- Que la empresa se empareje por dominio y no se duplique.
- Que la nota de IA falle sin tumbar el upsert (queda `aiNoteSuccess=false`).
- Que la propiedad de LinkedIn del contacto coincida con la del portal.
