"""Script Editor and Scene Browser commands: show/save the script, mark beats, rewrite one line."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from recap import config
from recap.llm import LLMError
from recap.models import StageStatus
from recap.script_edit import ScriptEditError, mark_beat, rewrite_line, save_script, show_script
from tests.test_cli import run_cli
from tests.test_script_stage import BEATS, FakeLLM, make_project

SCRIPT = {
    "title": "Cóc Kiện Trời",
    "target_duration_sec": 8,
    "words_per_sec": 3.0,
    "items": [
        {"id": "L0001", "type": "narrate", "text": "Ngày xưa có một chú cóc nhỏ sống trong hang tối.",
         "clips": [{"beat": "B0001", "start": "00:00:01", "end": "00:00:05"}]},
        {"id": "L0002", "type": "narrate", "text": "Gấu đỏ kể cho cóc nghe chuyện trời hạn hán.",
         "clips": [{"beat": "B0002", "start": "00:00:11", "end": "00:00:15.5"}]},
        {"id": "L0003", "type": "original", "beat": "B0003", "start": "00:00:26", "end": "00:00:30", "note": "gật đầu"},
    ],
}  # fmt: skip


class FakeStage:
    """Fingerprint = signature of one input file, like the real tts/assemble stages."""

    def __init__(self, name: str, uses: str) -> None:
        self.name = name
        self.uses = uses

    def outputs(self, project):  # noqa: ANN001, ANN201
        return []

    def fingerprint(self, project_dir: Path, project) -> str:  # noqa: ANN001
        return config.file_signature(project_dir / self.uses)


STAGES = {"script": FakeStage("script", "work/beat_marks.json"), "tts": FakeStage("tts", "script.yaml")}


def edit_project(tmp_path: Path) -> Path:
    """A project whose script, tts and later stages are done (scenes.json = the described beats)."""
    project_dir = make_project(tmp_path)
    scenes = [{k: v for k, v in b.items() if k != "desc"} for b in BEATS]
    (project_dir / "work/scenes.json").write_text(json.dumps(scenes, ensure_ascii=False))
    (project_dir / "script.yaml").write_text(yaml.safe_dump(SCRIPT, allow_unicode=True, sort_keys=False))
    project = config.load_project(project_dir)
    stages = {
        name: state.model_copy(update={
            "status": StageStatus.DONE,
            "fingerprint": STAGES[name].fingerprint(project_dir, project) if name in STAGES else "x",
        })
        for name, state in project.stages.items()
    }  # fmt: skip
    config.save_project(project_dir, project.model_copy(update={"stages": stages}))
    return project_dir


def statuses(project_dir: Path) -> dict[str, str]:
    return {n: s.status.value for n, s in config.load_project(project_dir).stages.items()}


# ---- show ------------------------------------------------------------------------------------


def test_show_returns_items_with_times_in_seconds(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)

    shown = show_script(project_dir, speech_rate=lambda settings: 3.5)

    assert shown["title"] == "Cóc Kiện Trời" and shown["words_per_sec"] == 3.0
    assert shown["voice_words_per_sec"] == 3.5
    assert shown["items"][1]["clips"] == [{"beat": "B0002", "start": 11.0, "end": 15.5}]
    assert shown["items"][2] == {"id": "L0003", "type": "original", "beat": "B0003", "start": 26.0, "end": 30.0,
                                 "note": "gật đầu"}  # fmt: skip


def test_show_without_script_is_an_error(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)

    with pytest.raises(ScriptEditError, match="script.yaml"):
        show_script(project_dir)


# ---- save ------------------------------------------------------------------------------------


def test_save_writes_the_edit_and_marks_later_stages_stale(tmp_path: Path) -> None:
    # Arrange
    project_dir = edit_project(tmp_path)
    items = show_script(project_dir, speech_rate=lambda s: 3.0)["items"]
    items[0] = {**items[0], "text": "Chú cóc nhỏ sống một mình trong hang sâu."}

    # Act
    stale = save_script(project_dir, items, STAGES)

    # Assert
    saved = yaml.safe_load((project_dir / "script.yaml").read_text())
    assert saved["items"][0]["text"] == "Chú cóc nhỏ sống một mình trong hang sâu."
    assert saved["items"][0]["clips"] == [{"beat": "B0001", "start": "00:00:01", "end": "00:00:05"}]
    assert saved["title"] == "Cóc Kiện Trời" and saved["words_per_sec"] == 3.0
    assert 'start: "00:00:01"' in (project_dir / "script.yaml").read_text()
    assert stale[0] == "tts" and "export" in stale
    assert statuses(project_dir)["script"] == "done"


def test_save_rejects_a_clip_outside_its_beat_and_keeps_the_file(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)
    before = (project_dir / "script.yaml").read_text()
    items = show_script(project_dir, speech_rate=lambda s: 3.0)["items"]
    items[1] = {**items[1], "clips": [{"beat": "B0002", "start": 30.0, "end": 35.0}]}

    with pytest.raises(ScriptEditError, match="nằm ngoài beat B0002"):
        save_script(project_dir, items, STAGES)

    assert (project_dir / "script.yaml").read_text() == before
    assert statuses(project_dir)["tts"] == "done"


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([], "items"),
        ([{"type": "narrate", "text": "Một câu.", "clips": [{"beat": "B0001", "start": 1, "end": 2}]}], "thiếu id"),
        ([{"id": "L1", "type": "original", "beat": "B0003", "start": 26, "end": 28}] * 2, "trùng id L1"),
    ],
)
def test_save_rejects_missing_or_duplicate_ids(tmp_path: Path, items: list, message: str) -> None:
    project_dir = edit_project(tmp_path)

    with pytest.raises(ScriptEditError, match=message):
        save_script(project_dir, items, STAGES)


# ---- beat marks ------------------------------------------------------------------------------


def test_mark_beat_saves_the_mark_and_marks_the_script_stale(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)

    stale = mark_beat(project_dir, "B0002", "star", STAGES)

    assert json.loads((project_dir / "work/beat_marks.json").read_text()) == {"B0002": "star"}
    assert stale[:2] == ["script", "tts"]


def test_mark_none_removes_the_mark(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)
    mark_beat(project_dir, "B0002", "star", STAGES)
    mark_beat(project_dir, "B0003", "skip", STAGES)

    mark_beat(project_dir, "B0002", "none", STAGES)

    assert json.loads((project_dir / "work/beat_marks.json").read_text()) == {"B0003": "skip"}


@pytest.mark.parametrize(("beat", "mark", "message"), [("B0999", "star", "B0999"), ("B0001", "love", "love")])
def test_mark_rejects_unknown_beat_or_mark(tmp_path: Path, beat: str, mark: str, message: str) -> None:
    project_dir = edit_project(tmp_path)

    with pytest.raises(ScriptEditError, match=message):
        mark_beat(project_dir, beat, mark, STAGES)


# ---- rewrite one line ------------------------------------------------------------------------


def test_rewrite_sends_the_line_its_scenes_and_neighbours(tmp_path: Path) -> None:
    # Arrange
    project_dir = edit_project(tmp_path)
    llm = FakeLLM(['"Gấu đỏ hớt hải chạy tới, báo tin trời đã ba năm không mưa."\n'])

    # Act
    text = rewrite_line(project_dir, "L0002", provider_factory=lambda s: llm, speech_rate=lambda s: 3.0, hint="kịch tính hơn")

    # Assert: quotes stripped; Claude saw the line, its beat and the lines around it
    assert text == "Gấu đỏ hớt hải chạy tới, báo tin trời đã ba năm không mưa."
    assert "Gấu đỏ kể cho cóc nghe" in llm.prompts[0] and "kịch tính hơn" in llm.prompts[0]
    assert "[B0002 00:00:10-00:00:25] Hình: Gấu đỏ nói chuyện" in llm.inputs[0]
    assert "chú cóc nhỏ sống trong hang tối" in llm.inputs[0]  # line before
    assert "Đồng ý" in llm.inputs[0]  # the original line after: its film dialogue
    assert "{" not in llm.prompts[0]  # every placeholder filled


def test_rewrite_asks_for_about_as_many_words_as_the_clip_fits(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)
    llm = FakeLLM(["Một câu mới cho dòng đầu tiên của phim này."])

    rewrite_line(project_dir, "L0001", provider_factory=lambda s: llm, speech_rate=lambda s: 3.0)

    assert "Khoảng 12 từ" in llm.prompts[0]  # 4 s of clip x 3 words/s


def test_rewrite_retries_once_on_an_empty_answer(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)
    llm = FakeLLM(["```\n```", "Câu mới đây rồi, đọc lên nghe hay hơn nhiều."])

    text = rewrite_line(project_dir, "L0001", provider_factory=lambda s: llm, speech_rate=lambda s: 3.0)

    assert text == "Câu mới đây rồi, đọc lên nghe hay hơn nhiều." and len(llm.prompts) == 2


def test_rewrite_reports_llm_errors_with_their_code(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)
    llm = FakeLLM([LLMError("llm_quota", "hết hạn mức", retryable=True)])

    with pytest.raises(ScriptEditError) as info:
        rewrite_line(project_dir, "L0001", provider_factory=lambda s: llm, speech_rate=lambda s: 3.0)

    assert info.value.code == "llm_quota" and info.value.retryable


@pytest.mark.parametrize("line", ["L0003", "L0999"])
def test_rewrite_only_accepts_narration_lines(tmp_path: Path, line: str) -> None:
    project_dir = edit_project(tmp_path)

    with pytest.raises(ScriptEditError, match=line):
        rewrite_line(project_dir, line, provider_factory=lambda s: FakeLLM([]), speech_rate=lambda s: 3.0)


# ---- CLI (what the app runs) -----------------------------------------------------------------


def test_cli_show_save_and_mark(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)

    code, events = run_cli("script-show", "--project", str(project_dir))
    assert code == 0, events
    items = events[-1]["data"]["items"]
    edit = tmp_path / "edit.json"
    edit.write_text(json.dumps({"items": [{**items[0], "text": "Câu đã sửa trong app."}, *items[1:]]}))

    code, events = run_cli("script-save", "--project", str(project_dir), "--file", str(edit))
    assert code == 0, events
    assert "tts" in events[-1]["data"]["stale"]
    assert "Câu đã sửa trong app." in (project_dir / "script.yaml").read_text()

    code, events = run_cli("beat-mark", "--project", str(project_dir), "--beat", "B0001", "--mark", "skip")
    assert code == 0, events
    assert events[-1]["data"]["marks"] == {"B0001": "skip"}


def test_cli_save_reports_invalid_script(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)
    edit = tmp_path / "edit.json"
    edit.write_text(json.dumps({"items": [{"id": "L1", "type": "narrate", "text": "", "clips": []}]}))

    code, events = run_cli("script-save", "--project", str(project_dir), "--file", str(edit))

    assert code == 1
    assert events[-1]["type"] == "error" and events[-1]["code"] == "script_invalid"


def test_rewrite_follows_the_same_guide_and_channel_voice(tmp_path: Path) -> None:
    project_dir = edit_project(tmp_path)
    llm = FakeLLM(["Một câu mới cho dòng đầu tiên của phim này."])

    rewrite_line(project_dir, "L0001", provider_factory=lambda s: llm, speech_rate=lambda s: 3.0)

    assert "Cẩm nang lời kể" in llm.prompts[0] and "Mắm" in llm.prompts[0]
