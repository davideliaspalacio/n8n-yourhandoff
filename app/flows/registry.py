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
KINDS = ("workflow", "node")


@dataclass(frozen=True)
class FlowSpec:
    """Describe un flow: con esto el registro lo monta como endpoint y el
    dashboard lo lista, sin que el flow toque routes.py."""

    slug: str  # kebab-case; también es el nombre con el que se guarda el run
    title: str
    input_model: type[BaseModel]
    run: Callable[..., Awaitable[Any]]  # async def run(clients, data) -> Any
    # "workflow": pipeline completo que se dispara desde fuera | "node": subflujo reutilizable
    kind: str = "workflow"
    mode: str = "sync"  # "sync": respuesta directa | "async": 202 + run_id
    replaces: str = ""  # workflow de n8n que sustituye
    summary: str = ""  # una línea para Swagger y el dashboard
    # Párrafo de contexto (2-4 frases) que se ve al entrar al flow en /ui
    description: str = ""
    response_model: Any = None  # opcional, solo flows sync
    # Grafo declarado para el dashboard: {"id", "label", "to": (ids siguientes)}.
    # "about" (opcional): qué hace el paso; se ve al hacer clic en el nodo en /ui.
    # Los ids deben coincidir con los nombres de record_step del flow.
    graph: tuple[dict, ...] = ()


def validate(spec: FlowSpec, *, origin: str) -> None:
    if not SLUG_RE.fullmatch(spec.slug or ""):
        raise RuntimeError(f"{origin}: slug inválido {spec.slug!r} (kebab-case: a-z, 0-9, '-')")
    if spec.mode not in MODES:
        raise RuntimeError(f"{origin}: mode inválido {spec.mode!r} (usa 'sync' o 'async')")
    if spec.kind not in KINDS:
        raise RuntimeError(f"{origin}: kind inválido {spec.kind!r} (usa 'workflow' o 'node')")
    if not (isinstance(spec.input_model, type) and issubclass(spec.input_model, BaseModel)):
        raise RuntimeError(f"{origin}: input_model debe ser un modelo Pydantic")
    _validate_graph(spec, origin=origin)


def _validate_graph(spec: FlowSpec, *, origin: str) -> None:
    if not spec.graph:
        return
    ids: list[str] = []
    for node in spec.graph:
        node_id = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node_id, str) or not node_id:
            raise RuntimeError(f"{origin}: graph con nodo sin id válido: {node!r}")
        if "about" in node and not (isinstance(node["about"], str) and node["about"].strip()):
            raise RuntimeError(f"{origin}: graph: 'about' de {node_id!r} debe ser un str no vacío")
        ids.append(node_id)
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise RuntimeError(f"{origin}: graph con ids duplicados {dup}")
    known = set(ids)
    targets: set[str] = set()
    for node in spec.graph:
        for target in node.get("to") or ():
            if target not in known:
                raise RuntimeError(
                    f"{origin}: graph: {node['id']!r} apunta a {target!r}, que no existe"
                )
            targets.add(target)
    if not known - targets:
        raise RuntimeError(f"{origin}: graph sin nodo raíz (hay un ciclo)")


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
