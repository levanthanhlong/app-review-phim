"""Script stage with a scripted fake LLM."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

from recap import config
from recap.events import Emitter
from recap.llm import LLMError
from recap.models import MediaInfo, Settings, StageStatus
from recap.runner import run_stage
from recap.stages.script import ScriptStage

# 3 beats spanning 40 s; target_ratio 0.2 -> target 8 s for the single part.
BEATS = [
    {"id": "B0001", "start": 0.0, "end": 10.0, "shots": [[0.0, 10.0]], "keyframe": "keyframes/B0001.jpg",
     "dialogue": "Đánh cóc thì trời đánh", "desc": "Cóc ngồi trong hang"},
    {"id": "B0002", "start": 10.0, "end": 25.0, "shots": [[10.0, 25.0]], "keyframe": "keyframes/B0002.jpg",
     "dialogue": "", "desc": "Gấu đỏ nói chuyện"},
    {"id": "B0003", "start": 25.0, "end": 40.0, "shots": [[25.0, 40.0]], "keyframe": "keyframes/B0003.jpg",
     "dialogue": "Đồng ý", "desc": "Ngọc Hoàng gật đầu"},
]  # fmt: skip

GOOD = """```yaml
items:
  - id: L0001
    type: narrate
    text: "Ngày xửa ngày xưa có một chú cóc nhỏ sống trong hang."
    clips:
      - {beat: B0001, start: "00:00:01", end: "00:00:05"}
  - id: L0002
    type: original
    beat: B0003
    start: "00:00:26"
    end: "00:00:30"
    note: "Ngọc Hoàng đồng ý"
```
SUMMARY: Cóc sống trong hang và kiện trời.
"""
BAD_BEAT = GOOD.replace("B0001", "B0999")


class FakeLLM:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.inputs: list[str] = []

    def generate(self, prompt: str, input_text: str) -> str:
        self.prompts.append(prompt)
        self.inputs.append(input_text)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_project(tmp_path: Path, beats: list[dict] = BEATS, duration: float = 40.0, ratio: float = 0.2) -> Path:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(
        tmp_path / "r", title="Cóc Kiện Trời", source=movie, settings=Settings(target_ratio=ratio)
    )
    (project_dir / "work/scenes_described.json").write_text(json.dumps(beats, ensure_ascii=False))
    project = config.load_project(project_dir)
    done = project.stages["describe"].model_copy(update={"status": StageStatus.DONE})
    stages = {n: (done if n in ("ingest", "transcribe", "scenes", "describe") else s) for n, s in project.stages.items()}
    config.save_project(project_dir, project.model_copy(update={"stages": stages, "media": MediaInfo(duration=duration)}))
    return project_dir


def run(project_dir: Path, llm: FakeLLM, force: bool = False, words_per_sec: float = 3.0) -> tuple[bool, list[dict]]:
    out = io.StringIO()
    stages = {"script": ScriptStage(provider_factory=lambda settings: llm, speech_rate=lambda settings: words_per_sec)}
    ok = run_stage(project_dir, "script", stages, Emitter(stream=out, raw_stream=io.StringIO()), force=force)
    return ok, [json.loads(line) for line in out.getvalue().splitlines()]


def test_writes_script_yaml_with_numbered_items(tmp_path: Path) -> None:
    # Arrange
    project_dir = make_project(tmp_path)
    llm = FakeLLM([GOOD])

    # Act
    ok, _ = run(project_dir, llm)

    # Assert
    assert ok
    script = yaml.safe_load((project_dir / "script.yaml").read_text())
    assert script["title"] == "Cóc Kiện Trời" and script["target_duration_sec"] == 8
    assert [i["id"] for i in script["items"]] == ["L0001", "L0002"]
    assert script["items"][1] == {"id": "L0002", "type": "original", "beat": "B0003", "start": "00:00:26",
                                  "end": "00:00:30", "note": "Ngọc Hoàng đồng ý"}  # fmt: skip
    assert "[B0001 00:00:00-00:00:10] Hình: Cóc ngồi trong hang" in llm.inputs[0]
    assert "Cóc Kiện Trời" in llm.prompts[0] and "8 giây" in llm.prompts[0]
    # every timecode is double-quoted so YAML 1.1 readers never turn "01:02:05" into a base-60 int
    raw_yaml = (project_dir / "script.yaml").read_text()
    assert 'start: "00:00:01"' in raw_yaml and 'end: "00:00:30"' in raw_yaml
    assert (project_dir / "work/digest/part_01.txt").is_file()
    assert (project_dir / "work/script_parts/part_01.yaml").is_file()


def test_invalid_answer_is_retried_with_error_feedback(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    llm = FakeLLM([BAD_BEAT, GOOD])

    ok, _ = run(project_dir, llm)

    assert ok and len(llm.prompts) == 2
    assert "B0999" in llm.prompts[1] and "lỗi" in llm.prompts[1].lower()


def test_gives_up_after_two_retries_and_keeps_raw(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    llm = FakeLLM([BAD_BEAT, BAD_BEAT, BAD_BEAT])

    ok, events = run(project_dir, llm)

    assert not ok and len(llm.prompts) == 3
    assert next(e for e in events if e["type"] == "error")["code"] == "script_invalid"
    assert "B0999" in (project_dir / "work/script_parts/part_01.raw.txt").read_text()


def test_quota_error_stops_stage_and_finished_parts_are_reused(tmp_path: Path) -> None:
    # Arrange: 2 parts (part_size 30 min, beats at 0 and 31 min)
    beats = [
        {**BEATS[0], "id": "B0001", "start": 0.0, "end": 10.0, "shots": [[0.0, 10.0]]},
        {**BEATS[1], "id": "B0002", "start": 1860.0, "end": 1870.0, "shots": [[1860.0, 1870.0]],
         "keyframe": "keyframes/B0002.jpg"},
    ]  # fmt: skip
    # total target 8 s split by beat duration -> 4 s per part, matching the 12-word answers
    project_dir = make_project(tmp_path, beats=beats, duration=1870.0, ratio=8 / 1870)
    part2 = GOOD.replace("B0001", "B0002").replace('"00:00:01"', '"00:31:01"').replace('"00:00:05"', '"00:31:05"')
    part2 = part2.split("  - id: L0002")[0] + "```\nSUMMARY: Phần hai.\n"
    part1 = GOOD.split("  - id: L0002")[0] + "```\nSUMMARY: Phần một.\n"

    # Act 1: part 1 ok, part 2 hits quota
    ok, events = run(project_dir, FakeLLM([part1, LLMError("llm_quota", "hết hạn mức", retryable=True)]))
    assert not ok
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "llm_quota" and err["retryable"] is True

    # Act 2: retry only needs part 2, and receives part 1's summary
    llm = FakeLLM([part2])
    ok, _ = run(project_dir, llm)

    # Assert
    assert ok and len(llm.prompts) == 1
    assert "Phần một." in llm.prompts[0] and "phần 2/2" in llm.prompts[0]
    items = yaml.safe_load((project_dir / "script.yaml").read_text())["items"]
    assert [i["clips"][0]["beat"] for i in items] == ["B0001", "B0002"]


def test_second_run_is_skipped(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))

    llm = FakeLLM([])
    ok, events = run(project_dir, llm)

    assert ok and events[-1]["skipped"] is True and llm.prompts == []


def test_changing_style_rewrites_script(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))
    project = config.load_project(project_dir)
    settings = project.settings.model_copy(update={"narration_style": "hai_mia_mai"})
    config.save_project(project_dir, project.model_copy(update={"settings": settings}))

    llm = FakeLLM([GOOD])
    ok, _ = run(project_dir, llm)

    assert ok and len(llm.prompts) == 1 and "hài hước" in llm.prompts[0]


def test_prompt_and_validation_use_the_voice_speaking_rate(tmp_path: Path) -> None:
    # GOOD = 12 words + 4 s original: at 6 words/s it is 6 s for an 8 s target (-25%)
    project_dir = make_project(tmp_path)
    llm = FakeLLM([GOOD, GOOD, GOOD])

    ok, events = run(project_dir, llm, words_per_sec=6.0)

    assert ok and len(llm.prompts) == 3
    assert "6 từ/giây" in llm.prompts[0] and "số từ / 6" in llm.prompts[0]
    assert "thêm nội dung" in llm.prompts[1]
    assert any(e["type"] == "log" and e["level"] == "warning" for e in events)
    assert yaml.safe_load((project_dir / "script.yaml").read_text())["words_per_sec"] == 6.0


def test_rate_is_shown_with_one_decimal(tmp_path: Path) -> None:
    llm = FakeLLM([GOOD])

    run(make_project(tmp_path), llm, words_per_sec=3.04)

    assert "3 từ/giây" in llm.prompts[0]
    llm = FakeLLM([GOOD])
    (tmp_path / "b").mkdir()
    run(make_project(tmp_path / "b"), llm, words_per_sec=2.75)
    assert "2.8 từ/giây" in llm.prompts[0]


def test_much_faster_voice_rewrites_script(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))

    llm = FakeLLM([GOOD])
    _, events = run(project_dir, llm, words_per_sec=4.0)  # +33%

    assert events[-1]["skipped"] is False and len(llm.prompts) == 1


def test_voice_with_similar_speed_keeps_script(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))

    llm = FakeLLM([])
    _, events = run(project_dir, llm, words_per_sec=3.3)  # +10%: within the 15% the tts stage tolerates

    assert events[-1]["skipped"] is True and llm.prompts == []


def test_default_speaking_rate_follows_voice_and_tts_rate() -> None:
    from recap.stages.script import settings_words_per_sec
    from recap.voices import words_per_sec

    s = Settings(tts_engine="vieneu", voice="Đức Trí", tts_rate="+10%")

    assert settings_words_per_sec(s) == pytest.approx(words_per_sec("vieneu", "Đức Trí", "+10%"))


def test_force_asks_claude_again_instead_of_reusing_saved_parts(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))

    llm = FakeLLM([GOOD.replace("chú cóc nhỏ", "chú cóc tí hon")])
    ok, _ = run(project_dir, llm, force=True)

    assert ok and len(llm.prompts) == 1
    assert "tí hon" in (project_dir / "script.yaml").read_text()


def test_prompt_includes_the_narration_guide(tmp_path: Path) -> None:
    # Arrange
    project_dir = make_project(tmp_path)
    llm = FakeLLM([GOOD])

    # Act
    ok, _ = run(project_dir, llm)

    # Assert: the guide's text reaches Claude and no placeholder is left unfilled
    assert ok
    assert "Cẩm nang lời kể" in llm.prompts[0]
    assert "{guide}" not in llm.prompts[0]


def test_editing_the_guide_rewrites_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))
    monkeypatch.setattr("recap.stages.script._guide", lambda: "Cẩm nang lời kể (bản mới)")

    # Act
    llm = FakeLLM([GOOD])
    ok, events = run(project_dir, llm)

    # Assert
    assert ok and events[-1]["skipped"] is False and len(llm.prompts) == 1
    assert "bản mới" in llm.prompts[0]


# ---- beats marked in the Scene Browser (work/beat_marks.json) -----------------------------


def write_marks(project_dir: Path, marks: dict[str, str]) -> None:
    (project_dir / "work/beat_marks.json").write_text(json.dumps(marks))


def test_skipped_beats_are_left_out_of_the_digest(tmp_path: Path) -> None:
    # Arrange
    project_dir = make_project(tmp_path)
    write_marks(project_dir, {"B0002": "skip"})
    llm = FakeLLM([GOOD])

    # Act
    ok, _ = run(project_dir, llm)

    # Assert
    assert ok
    assert "B0002" not in llm.inputs[0]
    assert "B0001" in llm.inputs[0] and "B0003" in llm.inputs[0]


def test_a_skipped_beat_cannot_be_used_by_the_script(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    write_marks(project_dir, {"B0001": "skip"})
    llm = FakeLLM([GOOD, GOOD.replace("B0001", "B0002").replace('"00:00:01"', '"00:00:11"').replace('"00:00:05"', '"00:00:15"')])

    ok, _ = run(project_dir, llm)

    assert ok and len(llm.prompts) == 2
    assert "B0001 không có trong đầu vào" in llm.prompts[1]


def test_beats_flagged_by_inspect_are_left_out_unless_kept(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    (project_dir / "work/inspect.json").write_text(json.dumps({"skip_beats": {"B0001": "credit", "B0002": "quảng cáo"}}))
    write_marks(project_dir, {"B0002": "keep"})
    llm = FakeLLM([GOOD.replace("B0001", "B0002").replace('"00:00:01"', '"00:00:11"').replace('"00:00:05"', '"00:00:15"')])

    ok, _ = run(project_dir, llm)

    assert ok and "B0001" not in llm.inputs[0] and "B0002" in llm.inputs[0]


def test_every_beat_skipped_says_which_were_skipped_by_inspect(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    (project_dir / "work/inspect.json").write_text(json.dumps({"skip_beats": {"B0001": "credit", "B0002": "credit"}}))
    write_marks(project_dir, {"B0003": "skip"})

    ok, events = run(project_dir, FakeLLM([]))

    assert not ok
    error = next(e for e in events if e["type"] == "error")
    assert "2 cảnh do bước Kiểm tra nguồn tự bỏ" in error["message"]


def test_starred_beats_are_flagged_and_the_prompt_explains_the_flag(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    write_marks(project_dir, {"B0002": "star"})
    llm = FakeLLM([GOOD])

    ok, _ = run(project_dir, llm)

    assert ok
    flagged = [line for line in llm.inputs[0].splitlines() if "⭐" in line]
    assert len(flagged) == 1 and flagged[0].startswith("[B0002 ")
    assert "⭐ QUAN TRỌNG" in llm.prompts[0]


def test_prompt_has_no_star_rule_without_starred_beats(tmp_path: Path) -> None:
    llm = FakeLLM([GOOD])

    run(make_project(tmp_path), llm)

    assert "⭐" not in llm.prompts[0] and "⭐" not in llm.inputs[0]


def test_marking_a_beat_rewrites_the_script(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))
    write_marks(project_dir, {"B0003": "star"})

    llm = FakeLLM([GOOD])
    _, events = run(project_dir, llm)

    assert events[-1]["skipped"] is False and len(llm.prompts) == 1


def test_fingerprint_without_marks_is_unchanged_by_the_marks_feature(tmp_path: Path) -> None:
    # projects made before beat marks existed must not see their script as outdated
    project_dir = make_project(tmp_path)
    stage = ScriptStage(speech_rate=lambda settings: 3.0)

    fingerprint = stage.fingerprint(project_dir, config.load_project(project_dir))

    assert "beat_marks" not in fingerprint


def test_marks_file_without_marks_keeps_old_fingerprint(tmp_path: Path) -> None:
    # starring a beat and then clearing it leaves "{}": the script must not count as outdated
    project_dir = make_project(tmp_path)
    stage = ScriptStage(speech_rate=lambda settings: 3.0)
    before = stage.fingerprint(project_dir, config.load_project(project_dir))

    write_marks(project_dir, {})

    assert stage.fingerprint(project_dir, config.load_project(project_dir)) == before


def test_rewriting_the_same_marks_keeps_the_fingerprint(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    stage = ScriptStage(speech_rate=lambda settings: 3.0)
    write_marks(project_dir, {"B0002": "star", "B0001": "skip"})
    before = stage.fingerprint(project_dir, config.load_project(project_dir))

    write_marks(project_dir, {"B0001": "skip", "B0002": "star"})  # same marks, new mtime and order

    assert stage.fingerprint(project_dir, config.load_project(project_dir)) == before


def test_rerun_backs_up_the_previous_script(tmp_path: Path) -> None:
    # Arrange: a script edited by hand (Script Editor) after the first run
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))
    edited = (project_dir / "script.yaml").read_text().replace("chú cóc nhỏ", "chú cóc đã sửa tay")
    (project_dir / "script.yaml").write_text(edited)

    # Act
    ok, events = run(project_dir, FakeLLM([GOOD]), force=True)

    # Assert: the new script replaced it, the edited one is kept and the user is told where
    assert ok and "đã sửa tay" not in (project_dir / "script.yaml").read_text()
    backups = list((project_dir / "work/script_backups").glob("script_*.yaml"))
    assert len(backups) == 1 and backups[0].read_text() == edited
    assert any(e["type"] == "log" and "script_backups" in e["message"] for e in events)


def test_rerun_with_an_identical_script_makes_no_backup(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))

    run(project_dir, FakeLLM([GOOD]), force=True)

    assert not (project_dir / "work/script_backups").exists()
