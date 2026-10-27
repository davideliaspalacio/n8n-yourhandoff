# Plataforma de flujos: de 1 workflow migrado a los 26 restantes

Fecha: 2026-10-26 · Estado: aprobado por David

## Objetivo

Convertir `handoff-flows` en la plataforma donde viven todos los workflows de
n8n migrados a código: un solo servicio FastAPI (monolito modular) donde cada
workflow es un paquete autocontenido que se registra solo, los flows se
componen entre sí como funciones Python, y un dashboard web muestra flujos,
ejecuciones, steps y costes sin tener que mirar código ni Swagger.

Quedan ~26 workflows por migrar. Todos son event-driven (webhooks/eventos,
duración de segundos a pocos minutos), como Discovery Call Booked. No hay
crons ni flujos de larga duración, así que no se adopta ningún motor de
orquestación (Temporal, Hatchet…) ni microservicios: se extiende el patrón
que ya funciona.

## Decisiones

- **Monolito modular**, un deploy en Railway, un Postgres, un Swagger.
  Descartados microservicios (overhead sin beneficio para este equipo/escala)
  y motores de orquestación (flujos cortos; el motor de runs propio ya da
  pasos, coste y dry_run).
- **Perder un evento en un deploy es aceptable por ahora** (decisión de
  David): se mantienen las background tasks en proceso. Si cambia, una cola
  respaldada en el mismo Postgres (p. ej. `procrastinate`) se añade por
  debajo del registro sin tocar los flows. El botón "Relanzar" del dashboard
  mitiga mientras tanto.
- **Dashboard v1: lectura + relanzar.** Sin activar/desactivar flujos ni
  edición de configuración desde la UI.
- **Composición en proceso**: un flow llama a otro importándolo, nunca por
  HTTP interno. El `ContextVar` `current_run` ya hace que pasos y costes del
  flow hijo caigan en el run del padre.
- La construcción se hace **por tracks paralelos** (ver "Fases y
  paralelización") para poder repartirla entre varios agentes.

## 1. `FlowSpec` y registro

Cada flow exporta un `FlowSpec` que lo describe:

```python
# app/flows/lead_scoring/flow.py
spec = FlowSpec(
    slug="lead-scoring",            # → POST /v1/flows/lead-scoring
    title="Lead Scoring",
    input_model=LeadScoringInput,   # Pydantic
    run=run,                        # async def run(input) -> output
    mode="async",                   # "async" → 202 + run_id | "sync" → respuesta directa
    replaces="Lead Scoring v2",     # workflow de n8n que sustituye
)
```

- `app/flows/registry.py` descubre los paquetes de `app/flows/` (import +
  convención: todo paquete con `flow.py` que exporte `spec`) y construye el
  registro en memoria al arrancar. Slug duplicado o spec inválido = el
  servicio no arranca (fallar en el deploy, no en producción).
- `api/routes.py` monta un endpoint por spec. El wrapper común hace lo que
  hoy hace a mano el orquestador: abre el run, ejecuta, registra steps y
  coste, cierra el run, respeta `dry_run`. Los endpoints actuales
  (`/v1/communications/search`, `/v1/crm/companies`…) se mantienen como
  alias para no romper a n8n ni al Founders Club Agent; los nuevos flows solo
  existen como `/v1/flows/<slug>`.
- Los 6 flows actuales se mueven al formato de paquete. Es refactorización:
  la lógica no cambia y los 41 tests siguen pasando (ajustando imports).

## 2. Convención de carpeta por flow

```
app/flows/<nombre>/
  __init__.py   # re-exporta spec y run
  flow.py       # spec + async def run(input) -> output
  schemas.py    # Pydantic de entrada/salida
  README.md     # 15-30 líneas: qué hace, qué clients/flows reusa,
                # qué workflow de n8n sustituye, qué verificar con credenciales reales
tests/flows/test_<nombre>.py
```

Tres niveles de reuso, como hasta ahora:

1. `clients/` — un archivo por API externa. Solo se crea un client nuevo
   cuando aparece un proveedor nuevo.
2. `domain/` — lógica pura sin I/O.
3. `flows/` — componen clients + domain y pueden llamar a otros flows como
   funciones.

Soporte al proceso:

- `scripts/new_flow.py <slug>` genera el esqueleto (carpeta, schemas, test,
  README con plantilla).
- `CLAUDE.md` en la raíz documenta la convención y el proceso de migración:
  es el "contexto pequeño" para que un agente migre el workflow #14 leyendo
  solo la convención, el JSON de n8n y un flow parecido.

## 3. Dashboard (`/ui`) y observabilidad

Servido por el mismo FastAPI. HTML + htmx (sin build step de frontend).
Autenticación: la misma `X-API-Key`; la UI la pide una vez y la guarda en
`localStorage`, cada fetch la manda en la cabecera. El shell HTML es público,
los datos no.

Vistas:

- **Flujos**: tabla desde el registro — slug, título, modo, qué sustituye en
  n8n, última ejecución, tasa de éxito y coste de los últimos 7 días.
- **Ejecuciones**: lista paginada y filtrable por flow/estado/fecha. Detalle
  de run: timeline de steps con su detail, input/output, error, costes por
  proveedor, y botón **Relanzar** (re-POST del input original como run nuevo
  enlazado al viejo).
- **Costes**: agregado por día y por proveedor desde `cost_events`.

Endpoints JSON nuevos que alimentan la UI (y sirven a cualquier otra
herramienta, p. ej. el MCP de handoff-tools):

- `GET /v1/flows` — el registro con stats recientes.
- `GET /v1/runs?flow=&status=&from=&to=&page=` — listado paginado.
- `POST /v1/runs/{run_id}/replay` — relanzar (guarda `replay_of` en el run nuevo).
- `GET /v1/costs/summary?group_by=day|provider` — agregados.

Logs: cada step ya queda en `flow_runs.steps`; el dashboard lo hace visible.
Además, logging estructurado JSON a stdout con `run_id`, `flow` y `step` en
cada línea, para correlacionar los logs de Railway con un run concreto.

Cambios de datos: columna `replay_of uuid null` en `flow_runs`
(migración `002`, idempotente como la 001). El resto del dashboard se sirve
de las tablas existentes.

## 4. Manejo de errores

- Igual que hoy: la política por paso vive en cada flow (qué es fatal y qué
  degrada); el wrapper del registro captura lo no manejado, marca el run
  `failed` con el error y los steps hasta ese punto.
- Al arrancar, los runs `running` huérfanos pasan a `failed` (ya existe);
  ahora son relanzables desde el dashboard.
- Flow que no cumple la convención (sin `spec`, slug duplicado, input no
  Pydantic): error al arrancar con mensaje claro.

## 5. Testing

- Por flow: tests con HTTP simulado (patrón actual), sin red.
- Del registro: tests de descubrimiento, montaje de rutas, wrapper
  (run/steps/coste/dry_run), colisión de slugs.
- Del dashboard: tests de los endpoints JSON; la UI se prueba a mano (es
  lectura + un POST).
- `uv run pytest` y `ruff check .` siguen siendo la barra para mergear.

## 6. Proceso repetible para los 26 workflows

Por cada workflow de n8n:

1. Exportar el JSON desde n8n.
2. `scripts/new_flow.py <slug>` y rellenar `replaces` en el spec.
3. Implementar reusando clients/domain/flows; client nuevo solo si hay API
   nueva.
4. Tests simulados; `uv run pytest` en verde.
5. Deploy; `dry_run: true` con un evento real; revisar el run en el dashboard.
6. En n8n, cambiar el workflow por un HTTP Request `POST /v1/flows/<slug>`
   con `X-API-Key` (único cambio en n8n).
7. Tras unos días estable, desactivar el workflow en n8n y marcarlo migrado.

Orden de migración: primero los que solo usan proveedores que ya tienen
client (HubSpot, Slack, Apollo, OpenAI, SerpApi, Fireflies, Unipile); los que
traen APIs nuevas, después, agrupados por proveedor para que un mismo agente
escriba el client y sus flows.

## 7. Fases y paralelización con agentes

La base (fases 1–2) se construye en tracks con fronteras de archivos
disjuntas para poder repartirla entre agentes en paralelo:

| Track | Entregable | Archivos | Depende de |
|---|---|---|---|
| A | `FlowSpec` + registro + wrapper + mover los 6 flows | `app/flows/*`, `app/api/routes.py` | — |
| B | Endpoints de listado + replay + migración 002 | `app/runs.py`, `app/api/`, `migrations/` | interfaz de A (solo `GET /v1/flows`) |
| C | UI del dashboard | `app/ui/` (templates + estáticos) | contratos JSON de B |
| D | Scaffold + `CLAUDE.md` + README | `scripts/`, raíz | convención de A |

A arranca primero y publica la interfaz de `FlowSpec`; B, C y D corren en
paralelo contra esa interfaz (C puede empezar con los contratos JSON
acordados antes de que B termine). Integración al final con los tests de
registro + dashboard.

Fase 3 (los 26 workflows) es paralelizable por naturaleza: cada workflow es
una carpeta aislada; se reparten en lotes entre agentes, con la regla de que
un client nuevo lo escribe un solo agente y los demás lo consumen.

## Fuera de alcance (por ahora)

- Cola persistente de eventos y reintentos automáticos.
- Activar/desactivar flujos o editar configuración desde la UI.
- Crons, flujos de larga duración, esperas con aprobación humana.
- Multi-tenancy o más de una API key.
