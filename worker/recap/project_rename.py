"""Renaming a project (`recap project-rename`): new title in project.json, folder renamed to match.

Safe for finished stages: fingerprints use file names, sizes and times (not the folder's path),
the source film is an absolute link, and render lists with absolute paths are rewritten on
every render. The app only renames when no job runs for the project.
"""

from __future__ import annotations

from pathlib import Path

from recap import config

MAX_TITLE = 120


def _clean(title: str) -> str:
    text = title.strip()
    if not text or "\n" in text or len(text) > MAX_TITLE:
        raise config.ProjectError("title_invalid", f"Tên dự án phải là một dòng, 1–{MAX_TITLE} ký tự")
    return text


def rename_project(project_dir: Path, title: str) -> Path:
    """Returns the project folder (new when its name changed)."""
    text = _clean(title)
    project = config.load_project(project_dir)
    target = project_dir.parent / f"{config.safe_folder_name(text)}{config.PROJECT_SUFFIX}"
    if target != project_dir:
        if target.exists():
            raise config.ProjectError("project_exists", f"Đã có dự án tên “{target.stem}”, hãy chọn tên khác")
        project_dir.rename(target)
    try:
        config.save_project(target, project.model_copy(update={"title": text}))
    except OSError:
        if target != project_dir:
            target.rename(project_dir)  # leave it as it was
        raise
    return target
