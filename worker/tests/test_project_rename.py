"""Renaming a project from the app's sidebar (`recap project-rename`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from recap import config
from recap.models import StageStatus
from recap.project_rename import rename_project
from tests.test_cli import run_cli


def make_project(tmp_path: Path, title: str = "Phim cũ") -> Path:
    movie = tmp_path / "film.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "root", title=title, source=movie)
    (project_dir / "work/scenes.json").write_text("[]")
    project = config.load_project(project_dir)
    done = project.stages["scenes"].model_copy(update={"status": StageStatus.DONE, "fingerprint": "f"})
    config.save_project(project_dir, project.with_stage("scenes", done))
    return project_dir


def test_rename_changes_the_title_and_moves_the_folder_with_everything_in_it(tmp_path: Path) -> None:
    old = make_project(tmp_path)

    new = rename_project(old, "  Thất Tình: bản mới ")

    assert new == old.parent / "Thất Tình- bản mới.recap" and not old.exists()
    project = config.load_project(new)
    assert project.title == "Thất Tình: bản mới"
    assert project.stages["scenes"].status == StageStatus.DONE  # nothing to redo
    assert (new / "work/scenes.json").read_text() == "[]"
    assert config.source_link(new).resolve() == (tmp_path / "film.mp4").resolve()


def test_changing_only_the_title_keeps_the_folder(tmp_path: Path) -> None:
    old = make_project(tmp_path, "Phim")

    new = rename_project(old, "Phim")  # same folder name

    assert new == old and config.load_project(new).title == "Phim"


def test_a_name_another_project_has_is_refused_and_nothing_changes(tmp_path: Path) -> None:
    old = make_project(tmp_path)
    (old.parent / "Phim khác.recap").mkdir()

    with pytest.raises(config.ProjectError) as failure:
        rename_project(old, "Phim khác")

    assert failure.value.code == "project_exists"
    assert old.exists() and config.load_project(old).title == "Phim cũ"


@pytest.mark.parametrize("title", ["", "   ", "hai\ndòng", "x" * 121])
def test_bad_titles_are_refused(tmp_path: Path, title: str) -> None:
    with pytest.raises(config.ProjectError) as failure:
        rename_project(make_project(tmp_path), title)

    assert failure.value.code == "title_invalid"


def test_cli_returns_the_new_folder(tmp_path: Path) -> None:
    old = make_project(tmp_path)

    code, events = run_cli("project-rename", "--project", str(old), "--title", "Tên mới")

    assert code == 0, events
    assert events[-1]["data"]["project"] == str(old.parent / "Tên mới.recap")
