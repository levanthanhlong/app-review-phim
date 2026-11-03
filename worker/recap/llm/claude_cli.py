"""LLM provider backed by the logged-in Claude Code CLI (`claude -p`), using the Pro plan quota."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from recap.llm.base import LLMError

TIMEOUT_SEC = 900.0
SYSTEM_PROMPT = (
    "Bạn là biên kịch kênh tóm tắt phim tiếng Việt. "
    "Chỉ trả về đúng nội dung được yêu cầu theo đúng định dạng, không giải thích thêm."
)
_QUOTA = re.compile(r"usage limit|rate limit|limit reached|quota|out of (extra )?usage", re.IGNORECASE)
_RESET_EPOCH = re.compile(r"\|(\d{9,})")
ERROR_TAIL_CHARS = 500


class Completed(Protocol):
    returncode: int
    stdout: str
    stderr: str


RunFn = Callable[[list[str], str, float], Completed]


@dataclass(frozen=True)
class _Result:
    returncode: int
    stdout: str
    stderr: str


def run_subprocess(cmd: list[str], stdin: str, timeout: float) -> Completed:
    """Run and wait; on cancellation (SIGTERM -> Cancelled) or timeout the CLI is killed."""
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
    )
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    return _Result(proc.returncode, out, err)


def _quota_message(text: str) -> str:
    match = _RESET_EPOCH.search(text)
    if match:
        reset = datetime.fromtimestamp(int(match.group(1)))
        return f"Hết hạn mức Claude, thử lại lúc {reset:%H:%M %d/%m}"
    return f"Hết hạn mức Claude: {text.strip()[:ERROR_TAIL_CHARS]}"


def _fail(text: str) -> LLMError:
    if _QUOTA.search(text):
        return LLMError("llm_quota", _quota_message(text), retryable=True)
    return LLMError("llm_failed", f"Claude CLI lỗi: {text.strip()[-ERROR_TAIL_CHARS:]}", retryable=True)


class ClaudeCLIProvider:
    def __init__(self, model: str, run: RunFn = run_subprocess, timeout: float = TIMEOUT_SEC) -> None:
        self._model = model
        self._run = run
        self._timeout = timeout

    def command(self, prompt: str) -> list[str]:
        return [
            "claude", "-p", prompt,
            "--model", self._model,
            "--output-format", "json",
            "--tools", "",  # text in, text out: no tool use
            "--setting-sources", "",  # ignore user hooks/settings
            "--strict-mcp-config",  # no MCP servers
            "--no-session-persistence",
            "--system-prompt", SYSTEM_PROMPT,
        ]  # fmt: skip

    def generate(self, prompt: str, input_text: str) -> str:
        try:
            done = self._run(self.command(prompt), input_text, self._timeout)
        except FileNotFoundError as exc:
            raise LLMError("llm_failed", "Không tìm thấy lệnh claude (Claude Code CLI)") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError("llm_failed", f"Claude CLI quá {self._timeout:.0f}s không trả lời", retryable=True) from exc

        try:
            data = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise _fail(f"{done.stdout}\n{done.stderr}") from None
        if not isinstance(data, dict):
            raise _fail(done.stdout)
        result = data.get("result")
        if done.returncode != 0 or data.get("is_error"):
            raise _fail(f"{result or ''}\n{done.stderr}")
        if not isinstance(result, str) or not result.strip():
            raise LLMError("llm_failed", "Claude CLI trả về kết quả rỗng", retryable=True)
        return result
