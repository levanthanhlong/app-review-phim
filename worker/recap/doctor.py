"""`recap doctor`: check tools, packages and storage the pipeline needs."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from recap.config import MIN_FREE_GB

MIN_PYTHON = (3, 11)
OLLAMA_URL = "http://127.0.0.1:11434/api/version"
OLLAMA_TIMEOUT_SEC = 1.0
GIB = 1024**3

TOOLS: dict[str, str] = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
    "claude": "Cài Claude Code CLI rồi đăng nhập: claude",
    "ollama": "brew install ollama",
}

# import name -> pip package name
PACKAGES: dict[str, str] = {
    "pydantic": "pydantic",
    "yaml": "pyyaml",
    "mlx_whisper": "mlx-whisper",
    "scenedetect": "scenedetect[opencv]",
    "cv2": "scenedetect[opencv]",
    "edge_tts": "edge-tts",
    "ollama": "ollama",
    "PIL": "pillow",
    "numpy": "numpy",
}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    required: bool = True


def check_python() -> Check:
    version = ".".join(map(str, sys.version_info[:3]))
    ok = sys.version_info[:2] >= MIN_PYTHON
    return Check("python", ok, f"{sys.executable} ({version})", "" if ok else "Cần Python 3.11+")


def check_tool(name: str, hint: str) -> Check:
    path = shutil.which(name)
    return Check(name, path is not None, path or "không tìm thấy trong PATH", "" if path else hint)


def check_packages() -> Check:
    missing = sorted({pip for module, pip in PACKAGES.items() if importlib.util.find_spec(module) is None})
    if missing:
        return Check("python_packages", False, "thiếu: " + ", ".join(missing), "cd worker && uv sync")
    return Check("python_packages", True, "đủ")


def check_ollama_server() -> Check:
    try:
        with urllib.request.urlopen(OLLAMA_URL, timeout=OLLAMA_TIMEOUT_SEC) as resp:
            return Check("ollama_server", True, resp.read().decode().strip(), required=False)
    except OSError:
        return Check("ollama_server", False, "chưa chạy", "ollama serve (cần từ stage describe)", required=False)


def _volume_of(path: Path) -> Path | None:
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return Path("/", parts[1], parts[2])
    return None


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists():
        current = current.parent
    return current


def check_storage(root: Path, min_free_gb: float = MIN_FREE_GB) -> Check:
    volume = _volume_of(root)
    if volume is not None and not os.path.ismount(volume):
        hint = f"Cắm ổ {volume.name} rồi thử lại"
        return Check("storage", False, f"storage_unavailable: {volume} chưa được gắn", hint)
    free_gb = shutil.disk_usage(_nearest_existing(root)).free / GIB
    detail = f"{root} — trống {free_gb:.0f}GB"
    if free_gb < min_free_gb:
        return Check("storage", False, detail, f"Cần trống ít nhất {min_free_gb}GB")
    return Check("storage", True, detail)


def run_checks(projects_root: Path, probe_ollama_server: bool = True) -> list[Check]:
    checks = [check_python(), *(check_tool(name, hint) for name, hint in TOOLS.items()), check_packages()]
    if probe_ollama_server:
        checks.append(check_ollama_server())
    checks.append(check_storage(projects_root))
    return checks


def overall_ok(checks: list[Check]) -> bool:
    return all(c.ok or not c.required for c in checks)


def as_result(checks: list[Check]) -> dict[str, Any]:
    return {"ok": overall_ok(checks), "checks": [asdict(c) for c in checks]}
