# LinkedIn Data

Sustituye al subflujo de n8n **Get LinkedIn Data**.

## Qué hace

`POST /v1/flows/linkedin-data` con `{companyUrl}` devuelve
`{foundLinkedIn, linkedIn, crm}` (Apollo organizations/enrich + `map_organization`).
Si Apollo no conoce la empresa, bloques vacíos y `foundLinkedIn=false`; un error de
Apollo falla la llamada (igual que dentro de enrich-company).

## Qué reusa

- clients: `apollo`
- flows/domain: `app.flows.company.linkedin_data` (misma función que enrich-company)

## Qué verificar con credenciales reales

Que el coste de Apollo (1 crédito si hay coincidencia) aparece en el run.
