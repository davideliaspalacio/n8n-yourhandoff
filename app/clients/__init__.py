"""Contenedor de clientes: se construye una vez por proceso y se pasa a los flujos."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import Settings
from .alma import AlmaClient
from .apollo import ApolloClient
from .event_function import EventFunctionClient
from .fireflies import FirefliesClient
from .hubspot import HubSpotClient
from .openai_llm import LLMClient
from .serpapi import SerpApiClient
from .slack import SlackClient
from .unipile import UnipileClient


@dataclass
class Clients:
    settings: Settings
    http: httpx.AsyncClient
    llm: LLMClient
    alma: AlmaClient
    apollo: ApolloClient
    event_function: EventFunctionClient
    fireflies: FirefliesClient
    hubspot: HubSpotClient
    serpapi: SerpApiClient
    slack: SlackClient
    unipile: UnipileClient

    @classmethod
    def build(cls, settings: Settings, http: httpx.AsyncClient | None = None) -> Clients:
        http = http or httpx.AsyncClient(timeout=settings.http_timeout_seconds)
        return cls(
            settings=settings,
            http=http,
            llm=LLMClient(settings),
            alma=AlmaClient(http, settings),
            apollo=ApolloClient(http, settings),
            event_function=EventFunctionClient(http, settings),
            fireflies=FirefliesClient(http, settings),
            hubspot=HubSpotClient(http, settings),
            serpapi=SerpApiClient(http, settings),
            slack=SlackClient(http, settings),
            unipile=UnipileClient(http, settings),
        )

    async def aclose(self) -> None:
        await self.http.aclose()
