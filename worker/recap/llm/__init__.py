"""LLM providers, chosen by `settings.llm_provider` so stages never change when the provider does."""

from __future__ import annotations

from recap.llm.base import LLMError, LLMProvider
from recap.llm.claude_cli import ClaudeCLIProvider
from recap.llm.codex_cli import REASONING, CodexCLIProvider
from recap.models import Settings

__all__ = ["LLMError", "LLMProvider", "get_provider"]


def model_key(settings: Settings) -> str:
    """Cache identity for the selected model, preserving existing Claude caches."""
    if settings.llm_provider == "codex_cli":
        return f"codex_cli:{settings.codex_model}:{REASONING}"
    return settings.claude_model


def model_label(settings: Settings) -> str:
    if settings.llm_provider == "codex_cli":
        return f"Codex ({settings.codex_model}, {REASONING})"
    return f"Claude ({settings.claude_model})"


def get_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "claude_cli":
        return ClaudeCLIProvider(model=settings.claude_model)
    if settings.llm_provider == "codex_cli":
        return CodexCLIProvider(model=settings.codex_model)
    raise LLMError("llm_unsupported", f"Provider '{settings.llm_provider}' chưa được hỗ trợ")
