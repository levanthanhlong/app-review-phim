"""`recap set`: validated settings changes that mark affected stages stale."""

from __future__ import annotations

from pathlib import Path

import pytest

from recap import config
from recap.models import StageStatus
from recap.runner import StageFailure, check_dependency
from recap.settings_update import SettingsError, apply_settings, parse_assignments
from tests.test_cli import run_cli


class FakeStage:
    def __init__(self, name: str, uses: str) -> None:
        self.name = name
        self.uses = uses

    def outputs(self, project):  # noqa: ANN001, ANN201
        return []

    def fingerprint(self, project_dir: Path, project) -> str:  # noqa: ANN001
        return str(getattr(project.settings, self.uses))


STAGES = {
    "ingest": FakeStage("ingest", "audio_track"),
    "transcribe": FakeStage("transcribe", "whisper_model"),
    "tts": FakeStage("tts", "voice"),
    "assemble": FakeStage("assemble", "duck_volume"),
}


def done_project(tmp_path: Path) -> Path:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)
    project = config.load_project(project_dir)
    stages = {}
    for name, state in project.stages.items():
        stage = STAGES.get(name)
        fingerprint = stage.fingerprint(project_dir, project) if stage else "x"
        stages[name] = state.model_copy(update={"status": StageStatus.DONE, "fingerprint": fingerprint})
    config.save_project(project_dir, project.model_copy(update={"stages": stages}))
    return project_dir


def statuses(project_dir: Path) -> dict[str, str]:
    return {n: s.status.value for n, s in config.load_project(project_dir).stages.items()}


@pytest.mark.parametrize(
    ("text", "key", "value"),
    [("voice=Đức Trí", "voice", "Đức Trí"), ("tts_rate=+5%", "tts_rate", "+5%"), ("target_ratio=0.25", "target_ratio", 0.25),
     ("audio_track=null", "audio_track", None), ("audio_track=1", "audio_track", 1), ("claude_model=sonnet", "claude_model", "sonnet")],
)  # fmt: skip
def test_parse_assignments(text: str, key: str, value: object) -> None:
    assert parse_assignments([text]) == {key: value}


@pytest.mark.parametrize("bad", ["voice", "=x", "khong_co=1"])
def test_parse_rejects_malformed_or_unknown(bad: str) -> None:
    with pytest.raises(SettingsError):
        parse_assignments([bad])


def test_changing_voice_marks_tts_and_everything_after_stale(tmp_path: Path) -> None:
    project_dir = done_project(tmp_path)

    settings, stale = apply_settings(project_dir, {"voice": "Đức Trí"}, STAGES)

    assert settings.voice == "Đức Trí"
    assert stale == ["tts", "music", "assemble", "export"]
    assert statuses(project_dir) == {"ingest": "done", "transcribe": "done", "scenes": "done", "inspect": "done", "characters": "done", "describe": "done",
                                     "script": "done", "tts": "stale", "music": "stale", "assemble": "stale",
                                     "export": "stale"}  # fmt: skip


def test_same_value_changes_nothing(tmp_path: Path) -> None:
    project_dir = done_project(tmp_path)
    voice = config.load_project(project_dir).settings.voice

    _, stale = apply_settings(project_dir, {"voice": voice}, STAGES)

    assert stale == [] and set(statuses(project_dir).values()) == {"done"}


def test_invalid_value_is_rejected_and_nothing_saved(tmp_path: Path) -> None:
    project_dir = done_project(tmp_path)
    before = (project_dir / "project.json").read_text()

    with pytest.raises(SettingsError, match="tts_engine"):
        apply_settings(project_dir, {"tts_engine": "piper"}, STAGES)

    assert (project_dir / "project.json").read_text() == before


def test_pending_stages_stay_pending(tmp_path: Path) -> None:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)

    _, stale = apply_settings(project_dir, {"voice": "Đức Trí"}, STAGES)

    assert stale == [] and set(statuses(project_dir).values()) == {"pending"}


def test_stale_previous_stage_still_satisfies_dependency(tmp_path: Path) -> None:
    project_dir = done_project(tmp_path)
    apply_settings(project_dir, {"voice": "Đức Trí"}, STAGES)
    project = config.load_project(project_dir)

    check_dependency(project, "music")  # tts is stale: its outputs exist, so the next stage may run
    pending = project.stages["tts"].model_copy(update={"status": StageStatus.PENDING})
    with pytest.raises(StageFailure):
        check_dependency(project.model_copy(update={"stages": {**project.stages, "tts": pending}}), "music")


def test_cli_set_reports_settings_and_stale(tmp_path: Path) -> None:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)

    code, events = run_cli("set", "--project", str(project_dir), "voice=Đức Trí", "tts_rate=+10%")

    assert code == 0, events
    data = events[-1]["data"]
    assert events[-1]["command"] == "set" and data["settings"]["voice"] == "Đức Trí" and data["stale"] == []
    assert config.load_project(project_dir).settings.tts_rate == "+10%"


def test_cli_set_rejects_bad_value(tmp_path: Path) -> None:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)

    code, events = run_cli("set", "--project", str(project_dir), "target_ratio=nhiều")

    assert code == 1 and events[-1]["code"] == "settings_invalid"


def test_cli_normalizes_decomposed_unicode_arguments(tmp_path: Path) -> None:
    # Foundation's Process passes arguments in NFD ("ứ" = u + horn + acute); catalogs use NFC
    import unicodedata

    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    nfd = lambda text: unicodedata.normalize("NFD", text)  # noqa: E731
    code, events = run_cli("new", "--root", str(tmp_path / "r"), "--title", nfd("Cóc Kiện Trời"), "--source", str(movie))
    assert code == 0, events
    project_dir = Path(events[-1]["data"]["project"])

    code, events = run_cli("set", "--project", str(project_dir), nfd("voice=Đức Trí"))

    assert code == 0, events
    project = config.load_project(project_dir)
    assert project.settings.voice == "Đức Trí" and project.title == "Cóc Kiện Trời"
    assert unicodedata.is_normalized("NFC", project.title)


class SourceGoneStage(FakeStage):
    """Like `ingest` once the film was deleted: its fingerprint cannot be computed."""

    def fingerprint(self, project_dir: Path, project) -> str:  # noqa: ANN001
        raise StageFailure("source_missing", "File phim gốc không còn tồn tại")


def test_a_stage_whose_fingerprint_cannot_be_computed_is_left_as_it_is(tmp_path: Path) -> None:
    # Arrange: the film was deleted after ingest (its outputs are still there)
    project_dir = done_project(tmp_path)
    stages = {**STAGES, "ingest": SourceGoneStage("ingest", "audio_track")}

    # Act
    _, stale = apply_settings(project_dir, {"voice": "Đức Trí"}, stages)

    # Assert: the change still goes through; ingest is not blamed for the missing film
    assert stale == ["tts", "music", "assemble", "export"]
    assert statuses(project_dir)["ingest"] == "done"
