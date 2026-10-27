"""Configuración por entorno. Ningún identificador de cuenta vive en el código.

Solo `API_KEY` es imprescindible para servir peticiones; el resto se valida
donde se usa, de modo que un proveedor sin configurar degrada su paso (o lo
falla con un mensaje claro) sin impedir que arranque el servicio.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Servicio
    api_key: str = ""
    database_url: str = ""
    http_timeout_seconds: float = 60.0
    http_max_attempts: int = 3
    # Segundos del primer reintento; se duplica en cada intento.
    http_retry_base_delay: float = 1.0
    # "json" en Railway (una línea JSON por log, con run_id); "text" en local.
    log_format: str = "text"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1"
    # USD por millón de tokens de gpt-4.1 (platform.openai.com/pricing)
    price_input_per_m: float = 2.00
    price_cached_input_per_m: float = 0.50
    price_output_per_m: float = 8.00

    # Supabase de Alma: tablas `accounts` y `handoff_prompt_library`
    alma_supabase_url: str = ""
    alma_supabase_key: str = ""
    team_names: str = "Operations,Sales,Executive,Technology,Talent"
    internal_email_domain: str = "yourhandoff.com"

    # Edge function que agrega al equipo al evento y devuelve sus datos
    event_function_url: str = ""
    # Cabecera de la credencial "Supabase Handoff Portal" de n8n.
    event_function_auth_header: str = "Authorization"
    event_function_auth_value: str = ""

    # Unipile (DSN propio de la cuenta, p. ej. https://apiN.unipile.com:PORT)
    unipile_base_url: str = ""
    unipile_api_key: str = ""
    unipile_emails_per_account: int = 5

    # Fireflies
    fireflies_api_key: str = ""
    fireflies_transcripts_per_email: int = 3

    # Apollo
    apollo_api_key: str = ""
    price_apollo_per_credit: float = 0.0

    # SerpApi (noticias y empleos)
    serpapi_api_key: str = ""
    price_serpapi_per_search: float = 0.0

    # HubSpot
    hubspot_token: str = ""
    hubspot_portal_id: str = ""
    # Propiedad de contacto donde va la URL de LinkedIn.
    hubspot_contact_linkedin_property: str = "hs_linkedin_url"

    # Slack de Handoff
    slack_bot_token: str = ""
    slack_channel_id: str = ""

    @property
    def team_list(self) -> list[str]:
        return [t.strip() for t in self.team_names.split(",") if t.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class NotConfigured(RuntimeError):
    """Falta una credencial para el proveedor que se intenta usar."""


def require(value: str, name: str) -> str:
    if not value:
        raise NotConfigured(f"{name} is not configured")
    return value
