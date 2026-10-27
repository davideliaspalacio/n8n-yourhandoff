---
name: crear-flujo
description: Proceso obligatorio para crear o migrar un workflow, flow o nodo en el repo handoff-flows (FastAPI). Úsala cuando pidan "necesito un workflow que haga X", migrar un workflow de n8n (con su export JSON), añadir un nodo reutilizable o un endpoint bajo /v1/flows. Garantiza reuso de clients, dominio y nodos existentes sin dañar lo que ya funciona.
---

# crear-flujo

Proceso para construir o migrar un flow en handoff-flows. Se apoya en
`CLAUDE.md` (convención del paquete, reglas del repo): léelo, no lo
dupliques. Sigue los pasos EN ORDEN; ninguno es opcional.

## 1. Contexto fresco

- Corre `uv run python scripts/catalog.py` y lee la salida completa.
- Lee `CLAUDE.md`.
- No asumas nada de memoria: el catálogo es la verdad de lo que existe hoy.

## 2. Reuso primero (reglas duras)

- Compón nodos existentes como funciones Python
  (`from app.flows.X import run_x`). NUNCA por HTTP interno, NUNCA copiando
  su lógica.
- Client nuevo SOLO si aparece un proveedor nuevo. Si el proveedor existe,
  añade métodos a su client siguiendo su estilo (`_request`, `record_cost`).
- Lógica pura reutilizable va en `app/domain/`; antes de escribirla, busca en
  el catálogo si ya existe.
- Los flows existentes son INTOCABLES, salvo dos excepciones: (a) extraer una
  función compartida preservando el comportamiento, (b) añadir `record_step`.
  La suite completa en verde es la prueba de no-ruptura. Si un test
  existente cambia, PARA y justifica por qué antes de seguir.

## 3. Scaffold

    uv run python scripts/new_flow.py <slug> --kind workflow|node [--replaces "<nombre en n8n>"]

`workflow` = proceso end-to-end con disparo externo; `node` = pieza
reutilizable que otros flows llaman como función.

## 4. Si es migración de n8n

- Lee el export JSON ENTERO antes de diseñar nada.
- Anota los bugs del original: no los repliques; documéntalos en el README
  del flow.
- Criterio de revisión: paridad semántica con el workflow de n8n.

## 5. Implementación

- Input/output en `schemas.py`.
- `record_step` por cada fase; los costes se registran solos vía los clients.
- `dry_run` es OBLIGATORIO si el flow escribe en sistemas externos: gatea solo
  las escrituras y devuelve campos `would_*`.

## 6. Checklist del FlowSpec (los tests del repo lo exigen)

- `description`: 2-4 frases.
- `graph`: ids iguales a los nombres de `record_step`; `about` en cada nodo.
- `kind` correcto (workflow vs node, ver paso 3).
- README del paquete: qué hace, qué reusa, qué sustituye.

## 7. Tests

- respx, sin red; sigue el patrón de `tests/flows/`.
- Happy path con asserts de los CUERPOS enviados a los proveedores.
- Caso de error del proveedor.
- Caso `dry_run` con cero escrituras.

## 8. Cierre

- `uv run pytest` y `uv run ruff check .` en verde.
- Actualiza el conteo de flows en `tests/test_registry.py`.
- Commit con paths explícitos (nunca `git add -A`).

## 9. Reporte final

Informa: qué se reusó, qué se creó, desviaciones del plan y qué verificar con
credenciales reales en el primer `dry_run`.
