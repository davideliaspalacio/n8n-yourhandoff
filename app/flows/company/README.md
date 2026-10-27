# enrich-company

Enriquece una empresa a partir de su URL: datos de Apollo, noticias y empleos de
Google (SerpApi) y un resumen en JSON generado por GPT-4.1.

- Tipo: `workflow`. Además de nodo del discovery call, es un workflow completo con entrada
  externa propia (webhook del Founders Club → `/v1/companies/enrich`).
- Endpoint: `POST /v1/flows/enrich-company` (alias antiguo `/v1/companies/enrich`).
- Entrada: `CompanyRequest` (`companyUrl`). Salida: `CompanyEnrichment`, que
  conserva la forma del webhook de n8n (`Company`, `linkedIn`, `crm`).

## Qué reusa
- Clients: `apollo` (organizations/enrich), `serpapi` (google_news, google_jobs),
  `alma` (prompt "Company Analysis (Discovery Prep)"), `llm`.
- Dominio: `to_hubspot_industry`, `company_name_from_url`, `domain_of`.
- `discovery_call` importa `enrich_company` desde este paquete.

## Qué sustituye
El workflow de n8n "Enrich Company" y sus subflujos "Get LinkedIn Data" y
"News + Job Search".

## Verificar con credenciales reales
- Que Apollo devuelva la organización y el mapeo a `linkedIn`/`crm` coincida con n8n.
- Que SerpApi degrade bien (sin clave o con error) sin romper el flow.
- Que `industryHubspot` case con las industrias reales de HubSpot.
