"""Script revision: the user's notes on the whole script, Claude rewrites it part by part."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

from recap import config
from recap.events import Emitter
from recap.models import StageStatus
from recap.script_edit import ScriptEditError
from recap.script_revise import revise_script
from recap.stages import STAGES
from recap.stages.script import ScriptStage
from tests.test_script_stage import GOOD, FakeLLM, make_project, run

FEEDBACK = "Câu mở đầu nhạt quá, cần gây tò mò hơn. Đừng dùng từ 'nhỏ'."
REVISED = GOOD.replace("Ngày xửa ngày xưa có một chú cóc nhỏ sống trong hang.",
                       "Ai ngờ một chú cóc sống trong hang lại dám kiện cả ông trời?")  # fmt: skip


def written(tmp_path: Path) -> Path:
    """A project whose script stage ran (with GOOD) and whose later stages are done."""
    project_dir = make_project(tmp_path)
    ok, events = run(project_dir, FakeLLM([GOOD]))
    assert ok, events
    project = config.load_project(project_dir)
    done = project.stages["tts"].model_copy(update={"status": StageStatus.DONE, "fingerprint": "x"})
    script = project.stages["script"].model_copy(update={"finished_at": "2026-01-01T00:00:00+00:00"})
    config.save_project(project_dir, project.with_stage("tts", done).with_stage("assemble", done).with_stage("script", script))
    return project_dir


def revise(project_dir: Path, llm: FakeLLM, feedback: str = FEEDBACK) -> tuple[list[str], list[dict]]:
    out = io.StringIO()
    # from script on: the fixture's earlier stages are DONE without a fingerprint
    later = {name: STAGES[name] for name in ("tts", "music", "assemble", "export")}
    stages = {"script": ScriptStage(provider_factory=lambda s: llm, speech_rate=lambda s: 3.0), **later}
    stale = revise_script(project_dir, feedback, stages, Emitter(stream=out, raw_stream=io.StringIO()),
                          provider_factory=lambda s: llm, speech_rate=lambda s: 3.0)  # fmt: skip
    return stale, [json.loads(line) for line in out.getvalue().splitlines()]


def script_text(project_dir: Path) -> str:
    return (project_dir / "script.yaml").read_text(encoding="utf-8")


def test_claude_gets_the_notes_and_the_current_script_and_its_answer_replaces_it(tmp_path: Path) -> None:
    # Arrange
    project_dir = written(tmp_path)
    before = config.load_project(project_dir).stages["script"]
    llm = FakeLLM([REVISED])

    # Act
    stale, events = revise(project_dir, llm)

    # Assert: the prompt has the notes, the input has the scenes and the current lines
    assert FEEDBACK in llm.prompts[0]
    assert "Cóc ngồi trong hang" in llm.inputs[0]  # scene of the digest
    assert "Ngày xửa ngày xưa có một chú cóc nhỏ" in llm.inputs[0]  # current line
    items = yaml.safe_load(script_text(project_dir))["items"]
    assert items[0]["text"].startswith("Ai ngờ") and [i["id"] for i in items] == ["L0001", "L0002"]
    # the old script is kept, later stages must run again, the editor sees a new script
    backups = list((project_dir / "work/script_backups").glob("*.yaml"))
    assert len(backups) == 1 and "chú cóc nhỏ" in backups[0].read_text(encoding="utf-8")
    assert {"tts", "assemble"} <= set(stale)
    after = config.load_project(project_dir).stages["script"]
    assert after.status == StageStatus.DONE and after.fingerprint == before.fingerprint
    assert after.finished_at != before.finished_at
    assert events[0]["type"] == "stage_start" and events[-1]["type"] == "stage_end"


def test_an_invalid_answer_is_retried_with_its_errors(tmp_path: Path) -> None:
    project_dir = written(tmp_path)
    llm = FakeLLM([REVISED.replace("B0001", "B0999"), REVISED])

    revise(project_dir, llm)

    assert len(llm.prompts) == 2 and "B0999" in llm.prompts[1] and FEEDBACK in llm.prompts[1]
    assert "Ai ngờ" in script_text(project_dir)


def test_when_claude_keeps_failing_the_script_is_left_as_it_was(tmp_path: Path) -> None:
    project_dir = written(tmp_path)
    original = script_text(project_dir)
    bad = REVISED.replace("B0001", "B0999")

    with pytest.raises(ScriptEditError) as failure:
        revise(project_dir, FakeLLM([bad, bad, bad]))

    assert failure.value.code == "script_invalid"
    assert script_text(project_dir) == original
    assert config.load_project(project_dir).stages["tts"].status == StageStatus.DONE


@pytest.mark.parametrize("feedback", ["", "   \n "])
def test_notes_are_required(tmp_path: Path, feedback: str) -> None:
    project_dir = written(tmp_path)

    with pytest.raises(ScriptEditError) as failure:
        revise(project_dir, FakeLLM([]), feedback=feedback)

    assert failure.value.code == "feedback_missing"


def test_no_script_yet_is_a_clear_error(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)

    with pytest.raises(ScriptEditError) as failure:
        revise(project_dir, FakeLLM([]))

    assert failure.value.code == "script_invalid"


def test_hand_edits_in_the_current_script_are_what_claude_revises(tmp_path: Path) -> None:
    project_dir = written(tmp_path)
    edited = script_text(project_dir).replace("Ngày xửa ngày xưa", "Chuyện kể rằng")
    (project_dir / "script.yaml").write_text(edited, encoding="utf-8")
    llm = FakeLLM([REVISED])

    revise(project_dir, llm)

    assert "Chuyện kể rằng" in llm.inputs[0]


def test_cli_reports_missing_notes_as_an_error_event(tmp_path: Path) -> None:
    from tests.test_cli import run_cli

    project_dir = written(tmp_path)

    code, events = run_cli("script-revise", "--project", str(project_dir), "--feedback", " ")

    assert code != 0 and events[-1]["type"] == "error" and events[-1]["code"] == "feedback_missing"
