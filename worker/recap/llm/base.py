"""Provider-agnostic LLM interface. Stages depend only on this."""

from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """code: llm_quota | llm_failed | llm_unsupported."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LLMProvider(Protocol):
    def generate(self, prompt: str, input_text: str) -> str:
        """Return the model's text answer to `prompt`, with `input_text` as the material to work on."""
        ...
