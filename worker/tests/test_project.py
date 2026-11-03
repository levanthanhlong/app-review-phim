import json
from pathlib import Path

import pytest

from recap import config
from recap.models import STAGE_NAMES, Project, Settings, StageStatus


def test_settings_defaults_match_spec() -> None:
    s = Settings()
    assert s.target_ratio == 0.2
    assert (s.tts_engine, s.voice, s.tts_rate) == ("vieneu", "Hải Đăng", "+0%")
    assert s.vl_model == "qwen2.5vl:7b"
    assert s.llm_provider == "claude_cli"
    assert (s.min_beat_sec, s.max_beat_sec, s.part_size_min) == (4, 20, 30)
    assert s.audio_track is None


def test_new_project_has_all_stages_pending() -> None:
    p = Project.new(title="Phim", source_path="/x/movie.mp4")
    assert list(p.stages) == list(STAGE_NAMES)
    assert all(st.status == StageStatus.PENDING for st in p.stages.values())


def test_project_is_immutable() -> None:
    p = Project.new(title="Phim", source_path="/x/movie.mp4")
    with pytest.raises(Exception):
        p.title = "khác"  # type: ignore[misc]


def test_create_project_makes_folder_symlink_and_json(tmp_path: Path) -> None:
    # Arrange
    movie = tmp_path / "Movie.MKV"
    movie.write_bytes(b"fake")
    root = tmp_path / "projects"

    # Act
    project_dir = config.create_project(root, title="Bố Già: Phần 1", source=movie)

    # Assert
    assert project_dir == root / "Bố Già- Phần 1.recap"
    link = project_dir / "source.mkv"
    assert link.is_symlink() and link.resolve() == movie.resolve()
    for sub in ("work", "output", "logs"):
        assert (project_dir / sub).is_dir()
    data = json.loads((project_dir / "project.json").read_text())
    assert data["title"] == "Bố Già: Phần 1"
    assert data["source_path"] == str(movie.resolve())
    assert data["stages"]["ingest"]["status"] == "pending"


def test_create_project_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(config.ProjectError) as exc:
        config.create_project(tmp_path, title="x", source=tmp_path / "nope.mp4")
    assert exc.value.code == "source_missing"


def test_create_project_rejects_existing(tmp_path: Path) -> None:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    config.create_project(tmp_path / "r", title="A", source=movie)
    with pytest.raises(config.ProjectError) as exc:
        config.create_project(tmp_path / "r", title="A", source=movie)
    assert exc.value.code == "project_exists"


def test_save_is_atomic_and_roundtrips(tmp_path: Path) -> None:
    # Arrange
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)
    project = config.load_project(project_dir)

    # Act
    updated = project.with_stage("ingest", project.stages["ingest"].model_copy(update={"status": StageStatus.DONE}))
    config.save_project(project_dir, updated)

    # Assert
    assert config.load_project(project_dir).stages["ingest"].status == StageStatus.DONE
    assert project.stages["ingest"].status == StageStatus.PENDING  # original untouched
    assert not list(project_dir.glob("*.tmp"))


def test_load_project_reports_missing(tmp_path: Path) -> None:
    with pytest.raises(config.ProjectError) as exc:
        config.load_project(tmp_path)
    assert exc.value.code == "project_missing"
