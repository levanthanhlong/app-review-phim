"""Saving the user's naming from the "Nhân vật" tab (`recap characters-save`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recap import config
from recap.character_edit import save_naming
from recap.characters import Choices, load_choices
from recap.models import StageStatus
from recap.script_edit import ScriptEditError
from recap.stages import STAGES
from recap.stages.script import ScriptStage
from tests.test_cli import run_cli
from tests.test_script_stage import GOOD, FakeLLM, make_project, run, write_characters


def named_project(tmp_path: Path) -> Path:
    project_dir = make_project(tmp_path)
    write_characters(project_dir)
    return project_dir


def stages() -> dict:
    later = {name: STAGES[name] for name in ("tts", "music", "assemble", "export")}
    return {"script": ScriptStage(provider_factory=lambda s: FakeLLM([]), speech_rate=lambda s: 3.0), **later}


def test_naming_is_saved_cleaned_and_confirmed(tmp_path: Path) -> None:
    project_dir = named_project(tmp_path)

    save_naming(project_dir, {"names": {"C01": "  Pan ", "C02": ""}, "merged": {}, "ignored": ["C02"]}, stages())

    assert load_choices(project_dir) == Choices(confirmed=True, names={"C01": "Pan"}, ignored=frozenset({"C02"}))


def test_a_new_naming_makes_the_written_script_stale(tmp_path: Path) -> None:
    from recap.characters import save_choices

    project_dir = named_project(tmp_path)
    save_choices(project_dir, Choices(confirmed=True))
    assert run(project_dir, FakeLLM([GOOD]))[0]

    stale = save_naming(project_dir, {"names": {"C01": "Pan"}}, stages())

    assert stale[0] == "script"
    assert config.load_project(project_dir).stages["script"].status == StageStatus.STALE


def test_skipping_saves_a_confirmation_without_names(tmp_path: Path) -> None:
    project_dir = named_project(tmp_path)

    save_naming(project_dir, {"names": {}, "skip": True}, stages())

    assert load_choices(project_dir) == Choices(confirmed=True)


@pytest.mark.parametrize(
    "raw",
    [
        {"names": {"C09": "Ai đó"}},  # no such character
        {"names": {"C01": "x" * 61}},  # name too long
        {"names": {"C01": "Pan\nHiro"}},  # one line only
        {"merged": {"C01": "C01"}},  # into itself
        {"merged": {"C02": "C07"}},  # into nobody
        {"ignored": ["C05"]},
        {"genders": {"C01": "khác"}},  # nam or nữ only
        {"genders": {"C09": "nam"}},
        {"names": ["C01", "Pan"]},  # wrong shape
    ],
)
def test_invalid_naming_is_rejected_and_nothing_saved(tmp_path: Path, raw: dict) -> None:
    project_dir = named_project(tmp_path)

    with pytest.raises(ScriptEditError) as failure:
        save_naming(project_dir, raw, stages())

    assert failure.value.code == "naming_invalid"
    assert not (project_dir / "work/character_choices.json").exists()


def test_without_the_characters_stage_there_is_nothing_to_name(tmp_path: Path) -> None:
    with pytest.raises(ScriptEditError) as failure:
        save_naming(make_project(tmp_path), {"names": {}}, stages())

    assert failure.value.code == "input_missing"


def test_cli_saves_the_naming_from_a_file(tmp_path: Path) -> None:
    project_dir = named_project(tmp_path)
    choices = tmp_path / "choices.json"
    choices.write_text(json.dumps({"names": {"C01": "Pan"}, "merged": {"C02": "C01"}}, ensure_ascii=False))

    code, events = run_cli("characters-save", "--project", str(project_dir), "--file", str(choices))

    assert code == 0, events
    assert events[-1]["data"]["choices"]["merged"] == {"C02": "C01"}
    assert load_choices(project_dir).names == {"C01": "Pan"}


def test_genders_are_saved_with_the_names(tmp_path: Path) -> None:
    project_dir = named_project(tmp_path)

    save_naming(project_dir, {"names": {"C01": "Pan"}, "genders": {"C01": "nữ", "C02": ""}}, stages())

    assert load_choices(project_dir).genders == {"C01": "nữ"}
