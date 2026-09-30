"""La única puerta a OpenAI: JSON mode, coste en el run y resumen normalizado."""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from openai import AsyncOpenAI

from ..config import Settings, require
from ..domain.summaries import Summary, extract_summary
from ..runs import record_cost

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class LLMResult:
    content: str
    summary: Summary
    response_id: str
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int
    prompt: str

    @property
    def usage(self) -> dict:
        return {
            "id": self.response_id,
            "model": self.model,
            "inputTokens": self.input_tokens,
            "cachedTokens": self.cached_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.input_tokens + self.cached_tokens + self.output_tokens,
            "costUsd": float(self.cost_usd),
        }


class LLMClient:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=require(self.settings.openai_api_key, "OPENAI_API_KEY"),
                timeout=self.settings.http_timeout_seconds,
                max_retries=self.settings.http_max_attempts - 1,
            )
        return self._client

    def cost(self, fresh_input: int, cached: int, output: int) -> Decimal:
        s = self.settings
        return (
            Decimal(fresh_input) / MILLION * Decimal(str(s.price_input_per_m))
            + Decimal(cached) / MILLION * Decimal(str(s.price_cached_input_per_m))
            + Decimal(output) / MILLION * Decimal(str(s.price_output_per_m))
        )

    async def complete_json(self, prompt: str, *, stage: str) -> LLMResult:
        started = time.perf_counter()
        response = await self.client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = response.usage
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        # OpenAI cuenta los tokens cacheados dentro de prompt_tokens: se separan
        # para cobrar cada parte a su precio.
        fresh = max((usage.prompt_tokens if usage else 0) - cached, 0)
        output = usage.completion_tokens if usage else 0
        cost = self.cost(fresh, cached, output)
        await record_cost(
            "openai",
            stage,
            fresh + cached + output,
            cost,
            {
                "model": response.model,
                "response_id": response.id,
                "input_tokens": fresh,
                "cached_tokens": cached,
                "output_tokens": output,
                "latency_ms": latency_ms,
            },
        )
        content = response.choices[0].message.content or ""
        return LLMResult(
            content=content,
            summary=extract_summary(content),
            response_id=response.id,
            model=response.model,
            input_tokens=fresh,
            cached_tokens=cached,
            output_tokens=output,
            cost_usd=cost,
            latency_ms=latency_ms,
            prompt=prompt,
        )
