"""LinkedIn Data: datos de la empresa por dominio (Apollo) en los bloques `linkedIn` y `crm`.

Sustituye al subflujo de n8n «Get LinkedIn Data». La lógica vive en
`app.flows.company.linkedin_data`; enrich-company usa la misma función.
"""

from __future__ import annotations

from app.clients import Clients
from app.flows.company import linkedin_data
from app.flows.registry import FlowSpec

from .schemas import LinkedinDataInput, LinkedinDataResult


async def run(clients: Clients, data: LinkedinDataInput) -> LinkedinDataResult:
    # El step "apollo" lo registra la función compartida.
    return LinkedinDataResult(**await linkedin_data(clients, data.companyUrl))


spec = FlowSpec(
    slug="linkedin-data",
    title="LinkedIn Data",
    input_model=LinkedinDataInput,
    response_model=LinkedinDataResult,
    run=run,
    kind="node",
    mode="sync",
    replaces="Get LinkedIn Data",
    summary="Get a company's LinkedIn-style and CRM data from its domain (Apollo)",
    description=(
        "Looks up a company by domain in Apollo and maps the organization to the linkedIn "
        "and crm blocks that the old n8n sub-workflow returned. It is the same function "
        "enrich-company uses. If Apollo does not know the company it returns empty blocks "
        "with foundLinkedIn false; Apollo errors fail the call."
    ),
    graph=(
        {
            "id": "apollo",
            "label": "Apollo organization",
            "about": (
                "Enriches the organization in Apollo from the domain and maps it to the "
                "linkedIn and crm blocks."
            ),
            "to": (),
        },
    ),
)
