"""LLM providers, chosen by `settings.llm_provider` so stages never change when the provider does."""

from __future__ import annotations

from recap.llm.base import LLMError, LLMProvider
from recap.llm.claude_cli import ClaudeCLIProvider
from recap.models import Settings

__all__ = ["LLMError", "LLMProvider", "get_provider"]


def get_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "claude_cli":
        return ClaudeCLIProvider(model=settings.claude_model)
    raise LLMError("llm_unsupported", f"Provider '{settings.llm_provider}' chưa được hỗ trợ (hiện chỉ có claude_cli)")
