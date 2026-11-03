"""Ollama vision-language client: start the server if needed, describe images, unload the model."""

from __future__ import annotations

import subprocess
import time
import urllib.request
from pathlib import Path

from recap import config

OLLAMA_HOST = "http://127.0.0.1:11434"
SERVER_START_TIMEOUT_SEC = 30.0
POLL_INTERVAL_SEC = 0.5
KEEP_ALIVE_WHILE_RUNNING = "5m"
GEN_OPTIONS = {"temperature": 0.2, "num_ctx": 4096, "num_predict": 120}
HTTP_NOT_FOUND = 404


class DescribeError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def server_is_up(host: str = OLLAMA_HOST) -> bool:
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=1.0):
            return True
    except OSError:
        return False


def ensure_server(host: str = OLLAMA_HOST, timeout: float = SERVER_START_TIMEOUT_SEC) -> None:
    """Start `ollama serve` in its own session (it outlives the worker) and wait until it answers."""
    if server_is_up(host):
        return
    config.configure_cache_env()  # models live on the external SSD
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise DescribeError("ollama_unavailable", "Chưa cài Ollama: brew install ollama") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_is_up(host):
            return
        time.sleep(POLL_INTERVAL_SEC)
    raise DescribeError("ollama_unavailable", "Không khởi động được Ollama (ollama serve)", retryable=True)


class OllamaDescriber:
    def __init__(self, model: str, host: str = OLLAMA_HOST) -> None:
        import ollama

        self._ollama = ollama
        self._model = model
        ensure_server(host)
        self._client = ollama.Client(host=host)
        self._check_model()

    def _check_model(self) -> None:
        try:
            self._client.show(self._model)
        except self._ollama.ResponseError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                raise DescribeError("model_missing", f"Chưa tải model. Chạy: ollama pull {self._model}") from exc
            raise DescribeError("describe_failed", f"Ollama lỗi: {exc}", retryable=True) from exc
        except ConnectionError as exc:
            raise DescribeError("ollama_unavailable", str(exc), retryable=True) from exc

    def describe(self, image: Path, prompt: str) -> str:
        message = {"role": "user", "content": prompt, "images": [str(image)]}
        try:
            response = self._client.chat(
                model=self._model, messages=[message], options=GEN_OPTIONS, keep_alive=KEEP_ALIVE_WHILE_RUNNING
            )
        except ConnectionError as exc:
            raise DescribeError("ollama_unavailable", str(exc), retryable=True) from exc
        except self._ollama.ResponseError as exc:
            raise DescribeError("describe_failed", f"Ollama lỗi: {exc}", retryable=True) from exc
        return response.message.content or ""

    def close(self) -> None:
        """Unload the model now (keep_alive=0) so the RAM is free for the next stage."""
        try:
            self._client.generate(model=self._model, keep_alive=0)
        except (ConnectionError, self._ollama.ResponseError):
            pass  # server already gone: nothing is loaded
