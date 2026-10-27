# Fireflies Summaries

Sustituye al subflujo de n8n **Get Fireflies Short Summaries**.

## Qué hace

`POST /v1/flows/fireflies-summaries` con `{emails}` devuelve
`{meeting_summaries, meeting_count}`: short summaries de las reuniones más recientes
por email, sin repetir reunión, de la más antigua a la más reciente. Un fallo de
Fireflies (o sin key) degrada a lista vacía con el step `degraded`.

## Qué reusa

- clients: `fireflies`
- flows/domain: `app.flows.team_communications.fetch_meetings` (misma función que
  team-communications)

## Qué verificar con credenciales reales

Que `summary.short_summary` viene poblado para reuniones recientes.
