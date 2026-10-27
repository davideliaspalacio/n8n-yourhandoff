# News + Job Search

Sustituye al subflujo de n8n **News + Job Search**.

## Qué hace

`POST /v1/flows/news-jobs-search` con `{companyName, companyUrl?}` devuelve
`{google_news, google_jobs}` (SerpApi). Si falla un motor (o falta la key) esa lista
queda vacía y el step queda `degraded`; no se rompe la llamada.

## Qué reusa

- clients: `serpapi`
- flows/domain: `app.flows.company.news_jobs_search` (misma función que enrich-company)

## Qué verificar con credenciales reales

Un coste de SerpApi por motor en el run.
