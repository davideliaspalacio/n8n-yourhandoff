# Desarrollo con AI

Todo el desarrollo de flows en este repo está pensado para hacerse con un
agente. La infraestructura que lo hace seguro vive en el propio repo.

## Cómo pedir un flujo

- Flujo nuevo: "necesito un workflow que haga X" (qué lo dispara, qué
  sistemas toca, qué devuelve).
- Migración: lo mismo, más el export JSON del workflow de n8n.
- El agente `flow-builder` (`.claude/agents/flow-builder.md`) toma el encargo
  y sigue la skill `crear-flujo`.

## Qué hace la skill `crear-flujo`

Vive en `.claude/skills/crear-flujo/SKILL.md`. Obliga a: leer el catálogo
vivo y `CLAUDE.md`; reusar nodos, clients y dominio existentes (nodos como
funciones, nunca por HTTP interno); generar el esqueleto con `new_flow.py`;
leer el export de n8n entero y documentar sus bugs; implementar con
`record_step` y `dry_run`; completar el `FlowSpec` (description, graph,
about); escribir tests; y reportar qué se reusó y qué verificar.

## Garantías

- Tests obligatorios con HTTP simulado (respx): happy path, error de
  proveedor y `dry_run` sin escrituras.
- Los flows existentes son intocables salvo extraer una función compartida o
  añadir `record_step`; la suite completa en verde lo comprueba.
- Revisión por paridad semántica con el workflow de n8n original.
- `dry_run` obligatorio en todo flow que escribe en sistemas externos.
- Dashboard automático: el flow aparece solo en `/ui`, Swagger y
  `GET /v1/flows`, con grafo, runs y costes.

## Comandos útiles

    uv run python scripts/catalog.py        # inventario vivo (flows, clients, dominio, settings)
    uv run python scripts/new_flow.py <slug> --kind workflow|node --replaces "..."
    uv run pytest && uv run ruff check .    # suite y lint
    /ui                                     # dashboard (runs, costes, mapa del flow)

El catálogo se genera por introspección del código: nunca se edita a mano y
nunca se desactualiza. Es lo primero que lee el agente antes de construir.
