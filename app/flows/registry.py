# app/flows/registry.py
"""Registro de flows.

Cada paquete de app/flows/ con un flow.py que exporte `spec` (FlowSpec) —o
`specs` cuando varios workflows pequeños comparten implementación— queda
registrado y montado como POST /v1/flows/<slug>. Un paquete mal formado
impide arrancar: fallar en el deploy, no en producción.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MODES = ("sync", "async")


@dataclass(frozen=True)
class FlowSpec:
    """Describe un flow: con esto el registro lo monta como endpoint y el
    dashboard lo lista, sin que el flow toque routes.py."""

    slug: str  # kebab-case; también es el nombre con el que se guarda el run
    title: str
    input_model: type[BaseModel]
    run: Callable[..., Awaitable[Any]]  # async def run(clients, data) -> Any
    mode: str = "sync"  # "sync": respuesta directa | "async": 202 + run_id
    replaces: str = ""  # workflow de n8n que sustituye
    summary: str = ""  # una línea para Swagger y el dashboard
    response_model: Any = None  # opcional, solo flows sync


def validate(spec: FlowSpec, *, origin: str) -> None:
    if not SLUG_RE.fullmatch(spec.slug or ""):
        raise RuntimeError(f"{origin}: slug inválido {spec.slug!r} (kebab-case: a-z, 0-9, '-')")
    if spec.mode not in MODES:
        raise RuntimeError(f"{origin}: mode inválido {spec.mode!r} (usa 'sync' o 'async')")
    if not (isinstance(spec.input_model, type) and issubclass(spec.input_model, BaseModel)):
        raise RuntimeError(f"{origin}: input_model debe ser un modelo Pydantic")


def add_specs(registry: dict[str, FlowSpec], specs: Iterable[FlowSpec], *, origin: str) -> None:
    for spec in specs:
        validate(spec, origin=origin)
        if spec.slug in registry:
            raise RuntimeError(f"slug duplicado {spec.slug!r} ({origin})")
        registry[spec.slug] = spec


def discover() -> dict[str, FlowSpec]:
    """Importa app/flows/<pkg>/flow.py de cada paquete y recoge sus specs.
    Los módulos sueltos (este mismo, o flows aún sin migrar) se ignoran."""
    import app.flows as flows_pkg

    registry: dict[str, FlowSpec] = {}
    for info in pkgutil.iter_modules(flows_pkg.__path__):
        if not info.ispkg:
            continue
        origin = f"app/flows/{info.name}/flow.py"
        module = importlib.import_module(f"app.flows.{info.name}.flow")
        specs = getattr(module, "specs", None)
        if specs is None:
            spec = getattr(module, "spec", None)
            specs = (spec,) if spec is not None else ()
        if not specs:
            raise RuntimeError(f"{origin} no exporta `spec` ni `specs`")
        add_specs(registry, specs, origin=origin)
    return registry
