"""Music stage, planning part: the LLM splits the recap into music cues (fake LLM, no audio needed)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import IO

from recap import config
from recap.events import Emitter
from recap.models import STAGE_NAMES, Settings, StageStatus
from recap.music_gen import GenRequest
from recap.runner import run_stage
from recap.stages.music import PLAN, MusicStage
from tests.test_assemble import BEATS, SCRIPT
from tests.test_script_stage import FakeLLM

GOOD = """```yaml
cues:
  - from: L0001
    to: L0003
    mood: hồi hộp
    prompt: "tense cinematic underscore, low strings, slow pulse, 80 bpm"
    alt_prompt: "sparse dark piano with soft ambient drones"
```"""
NAMED = GOOD.replace("low strings", "Toad theme, low strings")


def make_project(tmp_path: Path, source: str = "ai") -> Path:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    settings = Settings(music_source=source)  # type: ignore[arg-type]
    project_dir = config.create_project(tmp_path / "r", title="Cóc Kiện Trời", source=movie, settings=settings)
    (project_dir / "work/scenes.json").write_text(json.dumps(BEATS))
    (project_dir / "script.yaml").write_text(SCRIPT.replace("Câu một.", "Cóc gặp Toad trên đường."))
    tts_dir = project_dir / "work/tts"
    tts_dir.mkdir()
    for name in ("a.wav", "b.wav"):
        (tts_dir / name).write_bytes(b"RIFF")
    manifest = [{"id": "L0001", "file": "tts/a.wav", "duration": 5.75, "engine": "vieneu"},
                {"id": "L0003", "file": "tts/b.wav", "duration": 2.75, "engine": "vieneu"}]  # fmt: skip
    (tts_dir / "manifest.json").write_text(json.dumps(manifest))
    project = config.load_project(project_dir)
    done = project.stages["tts"].model_copy(update={"status": StageStatus.DONE})
    stages = {n: (done if STAGE_NAMES.index(n) < STAGE_NAMES.index("music") else s) for n, s in project.stages.items()}
    config.save_project(project_dir, project.model_copy(update={"stages": stages}))
    return project_dir


class SilentGenerator:
    """Writes a placeholder file instead of running a model."""

    engine = "fake"

    def available(self) -> bool:
        return True

    def model_name(self, req: GenRequest) -> str:
        return req.model

    def generate(self, req: GenRequest, out: Path, log: IO[str]) -> None:
        out.write_bytes(b"audio")

    def provenance(self) -> dict[str, str]:
        return {"engine": self.engine}


def run(project_dir: Path, llm: FakeLLM) -> tuple[bool, list[dict]]:
    out = io.StringIO()
    stages = {"music": MusicStage(provider_factory=lambda settings: llm, generators=[SilentGenerator()])}
    ok = run_stage(project_dir, "music", stages, Emitter(stream=out, raw_stream=io.StringIO()))
    return ok, [json.loads(line) for line in out.getvalue().splitlines()]


def test_music_stage_sits_between_tts_and_assemble() -> None:
    assert STAGE_NAMES.index("music") == STAGE_NAMES.index("tts") + 1 == STAGE_NAMES.index("assemble") - 1


def test_old_project_without_music_stage_gets_it_as_pending(tmp_path: Path) -> None:
    # Arrange: a project.json written before the music stage existed
    project_dir = make_project(tmp_path)
    data = json.loads((project_dir / "project.json").read_text())
    del data["stages"]["music"]
    (project_dir / "project.json").write_text(json.dumps(data))

    # Act
    project = config.load_project(project_dir)

    # Assert
    assert project.stages["music"].status == StageStatus.PENDING
    assert list(project.stages) == list(STAGE_NAMES)


def test_library_music_needs_no_plan_and_no_llm(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path, source="library")
    llm = FakeLLM([])

    ok, _ = run(project_dir, llm)

    assert ok and llm.prompts == []
    assert not (project_dir / PLAN).exists()


def test_ai_music_plans_cues_from_the_script(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    llm = FakeLLM([GOOD])

    ok, _ = run(project_dir, llm)

    assert ok and len(llm.prompts) == 1
    assert "[L0002 00:06-00:10 thoại gốc]" in llm.inputs[0]
    plan = json.loads((project_dir / PLAN).read_text())
    cue = plan["cues"][0]
    assert (cue["id"], cue["first_line"], cue["last_line"], cue["out_start"], cue["out_end"]) == ("M01", "L0001", "L0003", 0, 13)
    assert cue["prompt"].startswith("tense cinematic") and cue["chosen"] == 0


def test_prompt_naming_a_character_is_retried_with_the_error(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    llm = FakeLLM([NAMED, GOOD])

    ok, _ = run(project_dir, llm)

    assert ok and len(llm.prompts) == 2
    assert "toad" in llm.prompts[1].lower() and "lỗi" in llm.prompts[1].lower()


def test_gives_up_after_retries_and_keeps_the_raw_answer(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)

    ok, events = run(project_dir, FakeLLM([NAMED, NAMED, NAMED]))

    assert not ok
    assert [e["code"] for e in events if e["type"] == "error"] == ["music_plan_invalid"]
    assert (project_dir / "work/music/plan.raw.txt").read_text() == NAMED


def test_second_run_is_skipped(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeLLM([GOOD]))

    llm = FakeLLM([])
    ok, events = run(project_dir, llm)

    assert ok and events[-1]["skipped"] is True and llm.prompts == []
