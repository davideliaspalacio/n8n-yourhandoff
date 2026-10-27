#!/usr/bin/env python3
# scripts/new_flow.py
"""Genera el esqueleto de un flow nuevo según la convención del repo.

Uso: uv run python scripts/new_flow.py lead-scoring --replaces "Lead Scoring v2"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

FLOW_PY = '''"""{title}: TODO qué hace, en una línea.

Sustituye al workflow de n8n «{replaces}».
"""

from __future__ import annotations

import logging

from app.clients import Clients
from app.flows.registry import FlowSpec
from app.runs import record_step

from .schemas import {cls}Input, {cls}Result

logger = logging.getLogger(__name__)


async def run(clients: Clients, data: {cls}Input) -> {cls}Result:
    await record_step("todo", "running", {{"hint": "sustituir por los pasos reales"}})
    raise NotImplementedError("TODO: implementar {slug}")


spec = FlowSpec(
    slug="{slug}",
    title="{title}",
    input_model={cls}Input,
    response_model={cls}Result,
    run=run,
    mode="sync",  # "async" si debe responder 202 + run_id
    replaces="{replaces}",
    summary="TODO: one line for Swagger",
    # Mapa del proceso para el dashboard; ids = nombres de record_step.
    graph=(
        {{"id": "todo", "label": "TODO", "to": ()}},
    ),
)
'''

SCHEMAS_PY = '''from __future__ import annotations

from pydantic import BaseModel


class {cls}Input(BaseModel):
    pass  # TODO: campos del trigger de n8n


class {cls}Result(BaseModel):
    pass  # TODO: lo que devuelve el flow
'''

INIT_PY = """from .flow import run, spec
from .schemas import {cls}Input, {cls}Result

__all__ = ["{cls}Input", "{cls}Result", "run", "spec"]
"""

README = """# {title}

Sustituye al workflow de n8n **{replaces}**.

## Qué hace

TODO: 3-5 líneas.

## Qué reusa

- clients: TODO
- flows/domain: TODO

## Qué verificar con credenciales reales

TODO.
"""

TEST_PY = '''from app.flows.{pkg} import spec
from app.flows.registry import discover


def test_spec_registrado():
    assert discover()["{slug}"] is spec

# TODO: tests del flow con HTTP simulado (respx), como tests/test_flows.py.
'''


def create_flow(
    slug: str, *, title: str | None = None, replaces: str = "", root: Path = Path(".")
) -> list[Path]:
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"slug inválido {slug!r} (kebab-case: empieza por letra; a-z, 0-9, '-')")
    # title/replaces se interpolan dentro de literales "..." del código generado:
    # comillas o barras invertidas lo romperían.
    for name, value in (("title", title), ("replaces", replaces)):
        if value and ('"' in value or "\\" in value):
            raise ValueError(f'{name} no puede contener " ni \\: {value!r}')
    pkg = slug.replace("-", "_")
    cls = "".join(p.capitalize() for p in slug.split("-"))
    title = title or " ".join(p.capitalize() for p in slug.split("-"))
    pkg_dir = root / "app" / "flows" / pkg
    if pkg_dir.exists():
        raise FileExistsError(f"{pkg_dir} ya existe")
    pkg_dir.mkdir(parents=True)
    tests_dir = root / "tests" / "flows"
    tests_dir.mkdir(parents=True, exist_ok=True)
    ctx = {"slug": slug, "pkg": pkg, "cls": cls, "title": title, "replaces": replaces or "TODO"}
    files = {
        pkg_dir / "flow.py": FLOW_PY,
        pkg_dir / "schemas.py": SCHEMAS_PY,
        pkg_dir / "__init__.py": INIT_PY,
        pkg_dir / "README.md": README,
        tests_dir / f"test_{pkg}.py": TEST_PY,
    }
    for path, template in files.items():
        path.write_text(template.format(**ctx), encoding="utf-8")
    return list(files)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="kebab-case, p. ej. lead-scoring")
    parser.add_argument("--title", default=None)
    parser.add_argument("--replaces", default="", help="nombre del workflow de n8n")
    args = parser.parse_args()
    for path in create_flow(args.slug, title=args.title, replaces=args.replaces):
        print(f"  creado {path}")
    print("\nSiguiente: rellenar schemas.py, flow.py y el test. Ver CLAUDE.md.")


if __name__ == "__main__":
    main()
