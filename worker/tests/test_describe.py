"""Describe stage with a fake vision model (Ollama is exercised by the acceptance run)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from recap import config
from recap.events import Emitter
from recap.models import Settings, StageStatus
from recap.runner import run_stage
from recap.stages.describe import DescribeError, DescribeStage, build_prompt, clean_description

BEATS = [
    {"id": "B0001", "start": 0.0, "end": 5.0, "shots": [[0.0, 5.0]], "keyframe": "keyframes/B0001.jpg",
     "dialogue": "Đánh cóc thì trời đánh"},
    {"id": "B0002", "start": 5.0, "end": 12.0, "shots": [[5.0, 12.0]], "keyframe": "keyframes/B0002.jpg",
     "dialogue": ""},
    {"id": "B0003", "start": 12.0, "end": 20.0, "shots": [[12.0, 20.0]], "keyframe": "keyframes/B0003.jpg",
     "dialogue": "Vì sao vậy"},
]  # fmt: skip


class FakeDescriber:
    def __init__(self, fail_on: str | None = None, error: DescribeError | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False
        self.fail_on = fail_on
        self.error = error or DescribeError("describe_failed", "boom", retryable=True)

    def describe(self, image: Path, prompt: str) -> str:
        if self.fail_on and image.stem == self.fail_on:
            raise self.error
        self.calls.append((image.stem, prompt))
        return f'"Cóc xanh ngồi trong hang, trời mưa ({image.stem})."\nThêm dòng thừa'

    def close(self) -> None:
        self.closed = True


def make_project(tmp_path: Path, beats: list[dict] = BEATS) -> Path:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)
    write_scenes(project_dir, beats)
    project = config.load_project(project_dir)
    done = project.stages["scenes"].model_copy(update={"status": StageStatus.DONE})
    config.save_project(project_dir, project.model_copy(update={"stages": {n: done for n in project.stages}}))
    reset = project.stages["describe"]
    config.save_project(project_dir, config.load_project(project_dir).with_stage("describe", reset))
    return project_dir


def write_scenes(project_dir: Path, beats: list[dict]) -> None:
    (project_dir / "work/keyframes").mkdir(exist_ok=True)
    for b in beats:
        (project_dir / "work" / b["keyframe"]).write_bytes(b"jpg")
    (project_dir / "work/scenes.json").write_text(json.dumps(beats, ensure_ascii=False))


def run(project_dir: Path, fake: FakeDescriber, force: bool = False) -> tuple[bool, list[dict]]:
    out = io.StringIO()
    stages = {"describe": DescribeStage(describer_factory=lambda settings, emitter: fake)}
    ok = run_stage(project_dir, "describe", stages, Emitter(stream=out, raw_stream=io.StringIO()), force=force)
    return ok, [json.loads(line) for line in out.getvalue().splitlines()]


def described(project_dir: Path) -> list[dict]:
    return json.loads((project_dir / "work/scenes_described.json").read_text())


# ---- pure helpers -------------------------------------------------------------

def test_prompt_contains_dialogue_or_placeholder() -> None:
    assert "Đánh cóc thì trời đánh" in build_prompt("Đánh cóc thì trời đánh")
    assert "(không có thoại)" in build_prompt("")
    assert "{dialogue}" not in build_prompt("x")


def test_clean_description_takes_first_line_and_strips_quotes() -> None:
    assert clean_description('  "Cóc ngồi trong hang."\nrác') == "Cóc ngồi trong hang."


def test_clean_description_caps_word_count() -> None:
    text = " ".join(["từ"] * 60)
    assert len(clean_description(text).split()) == 40


# ---- stage ---------------------------------------------------------------------

def test_describes_every_beat_and_keeps_scene_fields(tmp_path: Path) -> None:
    # Arrange
    project_dir = make_project(tmp_path)
    fake = FakeDescriber()

    # Act
    ok, events = run(project_dir, fake)

    # Assert
    assert ok and fake.closed
    out = described(project_dir)
    assert [b["id"] for b in out] == ["B0001", "B0002", "B0003"]
    assert out[0]["desc"] == "Cóc xanh ngồi trong hang, trời mưa (B0001)."
    assert out[0]["dialogue"] == "Đánh cóc thì trời đánh" and out[0]["shots"] == [[0.0, 5.0]]
    assert "Đánh cóc thì trời đánh" in fake.calls[0][1]
    progress = [e for e in events if e["type"] == "progress"]
    assert progress[-1]["done"] == 3 and progress[-1]["total"] == 3 and progress[-1]["message"] == "B0003"


def test_second_run_is_skipped_and_force_uses_cache(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeDescriber())

    fake2 = FakeDescriber()
    _, events = run(project_dir, fake2)
    assert events[-1]["skipped"] is True

    fake3 = FakeDescriber()
    ok, _ = run(project_dir, fake3, force=True)
    assert ok and fake3.calls == []


def test_failure_keeps_finished_beats_and_resume_does_only_the_rest(tmp_path: Path) -> None:
    # Arrange: fail on the 3rd beat
    project_dir = make_project(tmp_path)
    ok, events = run(project_dir, FakeDescriber(fail_on="B0003"))
    assert not ok
    assert next(e for e in events if e["type"] == "error")["code"] == "describe_failed"
    assert config.load_project(project_dir).stages["describe"].status == StageStatus.FAILED

    # Act
    fake = FakeDescriber()
    ok, _ = run(project_dir, fake)

    # Assert
    assert ok and [c[0] for c in fake.calls] == ["B0003"]


def test_only_changed_beats_are_redescribed(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeDescriber())
    changed = [BEATS[0], {**BEATS[1], "dialogue": "Có thoại mới"}, BEATS[2]]
    write_scenes(project_dir, changed)

    fake = FakeDescriber()
    run(project_dir, fake)

    assert [c[0] for c in fake.calls] == ["B0002"]


def test_changing_model_redescribes_everything(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeDescriber())
    project = config.load_project(project_dir)
    config.save_project(project_dir, project.model_copy(update={"settings": Settings(vl_model="qwen2.5vl:3b")}))

    fake = FakeDescriber()
    run(project_dir, fake)

    assert len(fake.calls) == 3


@pytest.mark.parametrize("code,retryable", [("model_missing", False), ("ollama_unavailable", True)])
def test_describer_errors_become_stage_errors(tmp_path: Path, code: str, retryable: bool) -> None:
    project_dir = make_project(tmp_path)
    fake = FakeDescriber(fail_on="B0001", error=DescribeError(code, "msg", retryable=retryable))

    ok, events = run(project_dir, fake)

    err = next(e for e in events if e["type"] == "error")
    assert not ok and err["code"] == code and err["retryable"] is retryable
    assert fake.closed  # model is unloaded even on failure


def test_missing_keyframe_fails_clearly(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    (project_dir / "work/keyframes/B0002.jpg").unlink()

    ok, events = run(project_dir, FakeDescriber())

    assert not ok
    assert next(e for e in events if e["type"] == "error")["code"] == "input_missing"


# ---- who is on screen (named in the "Nhân vật" tab) ---------------------------


def test_unnamed_characters_stop_describe_before_the_long_work(tmp_path: Path) -> None:
    from tests.test_script_stage import write_characters

    project_dir = make_project(tmp_path)
    write_characters(project_dir)
    fake = FakeDescriber()

    ok, events = run(project_dir, fake)

    assert not ok and fake.calls == []
    assert next(e for e in events if e["type"] == "error")["code"] == "characters_unnamed"


def test_named_people_are_given_to_the_describer_to_trust_over_its_guess(tmp_path: Path) -> None:
    from recap.characters import Choices, save_choices
    from tests.test_script_stage import write_characters

    project_dir = make_project(tmp_path)
    write_characters(project_dir)  # C01 in every beat, C02 only in B0003
    save_choices(project_dir, Choices(confirmed=True, names={"C01": "Cha", "C02": "Hiro"}, genders={"C01": "nữ"}))
    fake = FakeDescriber()

    assert run(project_dir, fake)[0]
    prompts = dict(fake.calls)
    assert "Cha (nữ)" in prompts["B0001"] and "Hiro" not in prompts["B0001"]
    assert "Cha (nữ), Hiro" in prompts["B0003"]


def test_without_names_the_prompt_and_cache_stay_as_before(tmp_path: Path) -> None:
    from recap.characters import Choices, save_choices
    from tests.test_script_stage import write_characters

    project_dir = make_project(tmp_path)
    write_characters(project_dir)
    save_choices(project_dir, Choices(confirmed=True))  # "skip": no names
    fake = FakeDescriber()

    assert run(project_dir, fake)[0]
    assert dict(fake.calls)["B0001"] == build_prompt(BEATS[0]["dialogue"])


def test_renaming_redescribes_only_the_scenes_that_person_is_in(tmp_path: Path) -> None:
    from recap.characters import Choices, save_choices
    from tests.test_script_stage import write_characters

    project_dir = make_project(tmp_path)
    write_characters(project_dir)
    save_choices(project_dir, Choices(confirmed=True, names={"C01": "Cha", "C02": "Hiro"}))
    assert run(project_dir, FakeDescriber())[0]
    save_choices(project_dir, Choices(confirmed=True, names={"C01": "Cha", "C02": "Bat"}))
    fake = FakeDescriber()

    ok, events = run(project_dir, fake)

    assert ok and events[-1]["skipped"] is False
    assert [stem for stem, _ in fake.calls] == ["B0003"]
