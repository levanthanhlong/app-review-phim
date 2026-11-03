import json
from dataclasses import dataclass

import pytest

from recap.llm import LLMError, get_provider
from recap.llm.claude_cli import ClaudeCLIProvider
from recap.models import Settings


@dataclass
class Done:
    returncode: int
    stdout: str
    stderr: str = ""


class FakeRun:
    def __init__(self, result: Done) -> None:
        self.result = result
        self.cmd: list[str] = []
        self.stdin = ""

    def __call__(self, cmd: list[str], stdin: str, timeout: float) -> Done:
        self.cmd, self.stdin = cmd, stdin
        return self.result


def ok(text: str) -> Done:
    return Done(0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text}))


def test_returns_result_and_passes_input_on_stdin() -> None:
    run = FakeRun(ok("kịch bản"))
    provider = ClaudeCLIProvider(model="sonnet", run=run)

    assert provider.generate("PROMPT", "digest") == "kịch bản"
    assert run.stdin == "digest"
    assert run.cmd[:3] == ["claude", "-p", "PROMPT"]
    for flag in ("--model", "--output-format", "--tools", "--no-session-persistence", "--strict-mcp-config"):
        assert flag in run.cmd
    assert run.cmd[run.cmd.index("--tools") + 1] == ""  # no tools for the script writer


@pytest.mark.parametrize(
    "result",
    [
        Done(1, json.dumps({"type": "result", "is_error": True, "result": "Claude AI usage limit reached|1793700000"})),
        Done(1, "", "Error: 5-hour limit reached ∙ resets 3pm"),
        Done(0, json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "Rate limit exceeded"})),
    ],
)
def test_quota_errors(result: Done) -> None:
    with pytest.raises(LLMError) as exc:
        ClaudeCLIProvider(model="sonnet", run=FakeRun(result)).generate("p", "x")
    assert exc.value.code == "llm_quota" and exc.value.retryable is True


def test_quota_reset_time_is_reported() -> None:
    result = Done(1, json.dumps({"type": "result", "is_error": True, "result": "Claude AI usage limit reached|1793700000"}))
    with pytest.raises(LLMError) as exc:
        ClaudeCLIProvider(model="sonnet", run=FakeRun(result)).generate("p", "x")
    assert "thử lại lúc" in exc.value.message


@pytest.mark.parametrize("result", [Done(2, "segfault", "boom"), Done(0, "not json"), Done(0, json.dumps({"result": ""}))])
def test_other_failures(result: Done) -> None:
    with pytest.raises(LLMError) as exc:
        ClaudeCLIProvider(model="sonnet", run=FakeRun(result)).generate("p", "x")
    assert exc.value.code == "llm_failed"


def test_missing_cli_binary() -> None:
    def run(cmd: list[str], stdin: str, timeout: float) -> Done:
        raise FileNotFoundError("claude")

    with pytest.raises(LLMError) as exc:
        ClaudeCLIProvider(model="sonnet", run=run).generate("p", "x")
    assert exc.value.code == "llm_failed" and exc.value.retryable is False


def test_factory() -> None:
    assert isinstance(get_provider(Settings()), ClaudeCLIProvider)
    with pytest.raises(LLMError) as exc:
        get_provider(Settings(llm_provider="claude_api"))
    assert exc.value.code == "llm_unsupported"
