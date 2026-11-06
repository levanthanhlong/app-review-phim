import json

import pytest

from recap.llm import LLMError, get_provider, model_key
from recap.llm.codex_cli import CodexCLIProvider
from recap.models import Settings


def test_codex_reads_the_final_message_and_passes_prompt_via_stdin():
    def run(command, stdin, timeout):
        assert command[command.index("--model") + 1] == "gpt-6.1-sol"
        assert 'model_reasoning_effort="medium"' in command
        assert "read-only" in command and "--ignore-user-config" in command
        assert command[-1] == "-" and "Hướng dẫn" in stdin and "Cảnh phim" in stdin
        events = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Nháp"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Kịch bản"}},
            {"type": "turn.completed"},
        ]
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0, stdout="\n".join(map(json.dumps, events)), stderr="")

    assert CodexCLIProvider("gpt-6.1-sol", run=run).generate("Hướng dẫn", "Cảnh phim") == "Kịch bản"


def test_codex_fails_on_failed_turn_even_after_a_message():
    from types import SimpleNamespace

    output = '\n'.join([
        '{"type":"item.completed","item":{"type":"agent_message","text":"Nháp"}}',
        '{"type":"turn.failed","error":{"message":"usage limit reached"}}',
    ])
    provider = CodexCLIProvider("gpt-6.1-sol", run=lambda *_: SimpleNamespace(returncode=1, stdout=output, stderr=""))
    with pytest.raises(LLMError) as failure:
        provider.generate("prompt", "input")
    assert failure.value.code == "llm_quota"


def test_model_key_tracks_the_selected_backend_and_model():
    claude = Settings()
    codex = Settings(llm_provider="codex_cli")
    assert isinstance(get_provider(codex), CodexCLIProvider)
    assert model_key(claude) == claude.claude_model
    assert model_key(codex) != model_key(claude)
    assert model_key(codex.model_copy(update={"codex_model": "another-model"})) != model_key(codex)
    assert model_key(codex.model_copy(update={"claude_model": "opus"})) == model_key(codex)
