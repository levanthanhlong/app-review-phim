"""Text generation through the user's logged-in Codex CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from recap.llm.base import LLMError
from recap.llm.claude_cli import SYSTEM_PROMPT, TIMEOUT_SEC, RunFn, run_subprocess

_DESKTOP_CLI = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
REASONING = "medium"


class CodexCLIProvider:
    def __init__(self, model: str, run: RunFn = run_subprocess) -> None:
        self._model = model
        self._run = run

    def generate(self, prompt: str, input_text: str) -> str:
        executable = shutil.which("codex") or (str(_DESKTOP_CLI) if os.access(_DESKTOP_CLI, os.X_OK) else "codex")
        material = (
            f"{SYSTEM_PROMPT}\nDùng dữ liệu được cung cấp. Trả lời trực tiếp, không gọi công cụ.\n\n"
            f"{prompt}\n\n<Dữ liệu đầu vào>\n{input_text}\n</Dữ liệu đầu vào>"
        )
        try:
            with TemporaryDirectory(prefix="recap-codex-") as workspace:
                command = [
                    executable, "--ask-for-approval", "never", "exec", "--cd", workspace,
                    "--sandbox", "read-only", "--skip-git-repo-check", "--ignore-user-config",
                    "--ephemeral", "--json", "--model", self._model,
                    "-c", f'model_reasoning_effort="{REASONING}"', "-",
                ]
                done = self._run(command, material, TIMEOUT_SEC)
        except FileNotFoundError as exc:
            raise LLMError("llm_failed", "Không tìm thấy Codex CLI. Kiểm tra đường dẫn công cụ trong Cài đặt.") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError("llm_failed", "Codex CLI quá thời gian chờ.", retryable=True) from exc

        answer = ""
        completed = False
        failure = ""
        for line in done.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    answer = item.get("text", "")
            elif kind == "turn.completed":
                completed = True
            elif kind == "turn.failed":
                failure = str(event.get("error", ""))
            elif kind == "error" and not failure:
                failure = str(event.get("message", ""))
        if done.returncode == 0 and completed and answer.strip():
            return answer
        detail = failure or done.stderr.strip() or "Không nhận được kết quả hoàn chỉnh."
        lower = detail.lower()
        quota = any(term in lower for term in ("usage limit", "rate limit", "quota", "usage_limit_reached"))
        raise LLMError("llm_quota" if quota else "llm_failed", f"Codex CLI: {detail[-500:]}", retryable=True)
