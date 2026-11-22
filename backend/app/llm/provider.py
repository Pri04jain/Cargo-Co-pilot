"""
LLM provider wrapper.

Design goals:
  - The model name, API key, and provider all come from environment variables.
    Swapping from gpt-4o-mini to claude-3-haiku requires only a .env change.
  - Every call is retried with exponential backoff (tenacity) for transient errors.
  - Token counts and estimated cost are returned with every response so we can
    record them in the llm_calls table and show them in the UI.
  - Langfuse tracing is optional: if no keys are set, we skip it silently.

Why a thin wrapper instead of LangChain?
  LangChain adds abstraction but also magic. For a portfolio project where every
  design decision must be explainable, a 150-line wrapper is easier to defend
  than "LangChain handled it."

Cost estimation:
  Prices are approximate and change over time. We store them as constants here
  so they're easy to update. The eval harness reports real token counts alongside
  cost estimates — never fabricated numbers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Cost per 1M tokens (USD) — update when provider prices change ─────────────
# Source: OpenAI pricing page (approximate as of mid-2024)
COST_PER_1M: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 5.00, "output": 15.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
}


@dataclass
class LLMResponse:
    """Structured response from any LLM call."""
    content: str                    # raw text / JSON string from the model
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    trace_id: Optional[str] = None  # Langfuse trace ID


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Estimate cost in USD from token counts.
    Returns 0.0 if the model isn't in our price table — never crashes.
    """
    pricing = COST_PER_1M.get(model, {})
    if not pricing:
        return 0.0
    input_cost = (prompt_tokens / 1_000_000) * pricing.get("input", 0)
    output_cost = (completion_tokens / 1_000_000) * pricing.get("output", 0)
    return round(input_cost + output_cost, 8)


class LLMClient:
    """
    Thin wrapper around OpenAI (and Anthropic) that provides:
      - Retry with exponential backoff
      - Token and cost tracking
      - Langfuse tracing (optional)
      - Structured JSON output via response_format
    """

    def __init__(self) -> None:
        self.model = settings.llm_model
        self.provider = settings.llm_provider
        self._client = self._build_client()
        self._langfuse = self._build_langfuse()

    def _build_client(self):
        """Build the underlying API client based on LLM_PROVIDER env var."""
        if self.provider == "openai" or self.provider == "azure_openai":
            import openai
            return openai.OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url or None,
                timeout=settings.llm_timeout_seconds,
            )
        elif self.provider == "anthropic":
            import anthropic
            return anthropic.Anthropic(api_key=settings.llm_api_key)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def _build_langfuse(self):
        """Build Langfuse client if keys are configured, else return None."""
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            return None
        try:
            from langfuse import Langfuse
            return Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        except Exception as e:
            logger.warning("langfuse_init_failed", error=str(e))
            return None

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_format: Optional[dict] = None,  # JSON schema for structured output
        temperature: float = 0.0,
        max_tokens: int = 4096,
        trace_name: Optional[str] = None,
        prompt_version: Optional[str] = None,
        document_id: Optional[str] = None,
    ) -> LLMResponse:
        """
        Make a single LLM call with retry logic.

        temperature=0 for extraction (deterministic) — same document should
        produce the same extraction on every run, making bugs reproducible.
        """
        start_ms = int(time.time() * 1000)
        trace_id = None

        # Start Langfuse trace if available
        trace = None
        if self._langfuse and trace_name:
            try:
                trace = self._langfuse.trace(
                    name=trace_name,
                    metadata={
                        "model": self.model,
                        "prompt_version": prompt_version,
                        "document_id": document_id,
                    },
                )
                trace_id = trace.id
            except Exception:
                pass  # tracing is non-critical

        try:
            response = self._call_with_retry(
                system=system,
                user=user,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        finally:
            if trace and self._langfuse:
                try:
                    self._langfuse.flush()
                except Exception:
                    pass

        latency_ms = int(time.time() * 1000) - start_ms

        # Extract token usage (provider-specific)
        prompt_tokens, completion_tokens = self._extract_usage(response)
        total_tokens = prompt_tokens + completion_tokens
        cost = estimate_cost(self.model, prompt_tokens, completion_tokens)
        content = self._extract_content(response)

        llm_response = LLMResponse(
            content=content,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            trace_id=trace_id,
        )

        logger.info(
            "llm_call_complete",
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            trace_name=trace_name,
        )

        # Log to Langfuse generation
        if trace:
            try:
                trace.generation(
                    name=trace_name or "llm_call",
                    model=self.model,
                    input={"system": system[:500], "user": user[:500]},
                    output=content[:1000],
                    usage={"input": prompt_tokens, "output": completion_tokens},
                    metadata={"cost_usd": cost, "latency_ms": latency_ms},
                )
            except Exception:
                pass

        return llm_response

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _call_with_retry(
        self,
        system: str,
        user: str,
        response_format: Optional[dict],
        temperature: float,
        max_tokens: int,
    ):
        """Inner call — retried by tenacity on any exception."""
        if self.provider in ("openai", "azure_openai"):
            return self._call_openai(system, user, response_format, temperature, max_tokens)
        elif self.provider == "anthropic":
            return self._call_anthropic(system, user, temperature, max_tokens)

    def _call_openai(self, system, user, response_format, temperature, max_tokens):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # Structured JSON output — only supported on certain models
        if response_format:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction_result",
                    "strict": True,
                    "schema": response_format,
                },
            }
        elif settings.llm_base_url:
            # OpenAI-compatible endpoints (Ollama, Groq, vLLM, etc.): force JSON
            # mode so small models emit syntactically valid JSON instead of prose.
            kwargs["response_format"] = {"type": "json_object"}
        return self._client.chat.completions.create(**kwargs)

    def _call_anthropic(self, system, user, temperature, max_tokens):
        return self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )

    def _extract_content(self, response) -> str:
        """Extract the text content from a provider response object."""
        if self.provider in ("openai", "azure_openai"):
            # Surface truncation: if the model hit the token cap the JSON will
            # be cut mid-string, so make that visible in the logs.
            try:
                finish_reason = response.choices[0].finish_reason
                if finish_reason != "stop":
                    logger.warning(
                        "llm_response_truncated",
                        finish_reason=finish_reason,
                        model=self.model,
                    )
            except Exception:
                pass
            return response.choices[0].message.content or ""
        elif self.provider == "anthropic":
            return response.content[0].text
        return ""

    def _extract_usage(self, response) -> tuple[int, int]:
        """Extract (prompt_tokens, completion_tokens) from a provider response."""
        try:
            if self.provider in ("openai", "azure_openai"):
                usage = response.usage
                return usage.prompt_tokens, usage.completion_tokens
            elif self.provider == "anthropic":
                usage = response.usage
                return usage.input_tokens, usage.output_tokens
        except Exception:
            pass
        return 0, 0


# ── Module-level singleton — one client per worker process ────────────────────
# Lazy-initialised so tests can mock settings before the client is created.
_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm_client() -> None:
    """Used in tests to force re-initialisation with mocked settings."""
    global _client
    _client = None
