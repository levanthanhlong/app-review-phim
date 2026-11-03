"""Project folder layout, creation, and atomic load/save of project.json."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from recap.models import Project, Settings

EXTERNAL_VOLUME = Path("/Volumes/ExternalSSD")
HF_HOME_DIR = EXTERNAL_VOLUME / "hf-cache"
OLLAMA_MODELS_DIR = EXTERNAL_VOLUME / "ollama-models"
REPO_ROOT = Path(__file__).resolve().parents[2]  # appMReviewphim/
DEFAULT_PROJECTS_ROOT = Path(os.environ.get("RECAP_PROJECTS_ROOT", str(REPO_ROOT / "output")))
MIN_FREE_GB = 10
PROJECT_FILE = "project.json"
PROJECT_SUFFIX = ".recap"
SUBDIRS = ("work", "output", "logs")

_UNSAFE_NAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


class ProjectError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def safe_folder_name(title: str) -> str:
    cleaned = _UNSAFE_NAME_CHARS.sub("-", title).strip().strip(".")
    return cleaned or "Untitled"


def source_link(project_dir: Path) -> Path:
    """The `source.<ext>` symlink inside a project folder."""
    matches = sorted(project_dir.glob("source.*"))
    if not matches:
        raise ProjectError("source_missing", f"Không tìm thấy symlink source.* trong {project_dir}")
    return matches[0]


def create_project(root: Path, title: str, source: Path, settings: Settings | None = None) -> Path:
    source = source.expanduser()
    if not source.is_file():
        raise ProjectError("source_missing", f"Không tìm thấy file phim: {source}")
    project_dir = root / f"{safe_folder_name(title)}{PROJECT_SUFFIX}"
    if project_dir.exists():
        raise ProjectError("project_exists", f"Dự án đã tồn tại: {project_dir}")

    resolved = source.resolve()
    project_dir.mkdir(parents=True)
    for sub in SUBDIRS:
        (project_dir / sub).mkdir()
    (project_dir / f"source{resolved.suffix.lower()}").symlink_to(resolved)
    save_project(project_dir, Project.new(title=title, source_path=str(resolved), settings=settings))
    return project_dir


def load_project(project_dir: Path) -> Project:
    path = project_dir / PROJECT_FILE
    if not path.is_file():
        raise ProjectError("project_missing", f"Không tìm thấy {path}")
    try:
        return Project.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ProjectError("project_invalid", f"{path} không hợp lệ: {exc}") from exc


def save_project(project_dir: Path, project: Project) -> None:
    write_text_atomic(project_dir / PROJECT_FILE, json.dumps(project.model_dump(mode="json"), ensure_ascii=False, indent=2))


def write_text_atomic(path: Path, text: str) -> None:
    """Write to `<name>.tmp` then rename, so readers never see a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=1))


def file_signature(path: Path) -> str:
    """Cheap identity of a file's content version (size + mtime). Missing file -> 'missing'."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return f"{path.name}:missing"
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def configure_cache_env() -> None:
    """Keep downloaded models on the external SSD (internal disk is small). Explicit env wins."""
    if EXTERNAL_VOLUME.is_dir():
        os.environ.setdefault("HF_HOME", str(HF_HOME_DIR))
        os.environ.setdefault("OLLAMA_MODELS", str(OLLAMA_MODELS_DIR))
