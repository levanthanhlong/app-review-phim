"""Stage `characters` on a generated clip; face recognition and the LLM are faked."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from recap import config
from recap.characters import Choices, Face, load_choices, save_choices
from recap.events import Emitter
from recap.models import StageStatus
from recap.runner import run_stage
from recap.stages.characters import CHARACTERS, PICTURES_DIR, CharactersStage
from tests.conftest import HAS_FFMPEG, make_clip
from tests.test_cli import run_cli

DURATION = 12
PEOPLE = {name: v / np.linalg.norm(v) for name, v in ((n, np.random.default_rng(i).normal(size=128)) for i, n in enumerate(("pan", "hiro")))}


def test_sharp_lit_portraits_rank_ahead_of_blurry_or_dark_ones() -> None:
    from recap.stages.characters import _picture_score

    grid = np.indices((80, 80)) // 8
    sharp = Image.fromarray((((grid[0] + grid[1]) % 2) * 255).astype(np.uint8))
    blurred = sharp.filter(ImageFilter.GaussianBlur(2))
    dark = sharp.point(lambda value: value // 8)

    assert _picture_score(sharp) > _picture_score(blurred) > _picture_score(dark)


class FakeReader:
    """Pan in every frame; Hiro from 4 s to 7 s."""

    def __init__(self) -> None:
        self.calls = 0

    def read(self, image: Path, time: float) -> list[Face]:
        self.calls += 1
        faces = [Face(time=time, box=(0.1, 0.2, 0.2, 0.3), score=0.95, feat=PEOPLE["pan"])]
        if 4 <= time <= 7:
            faces.append(Face(time=time, box=(0.6, 0.2, 0.2, 0.3), score=0.95, feat=PEOPLE["hiro"]))
        return faces


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, input_text: str) -> str:
        self.calls += 1
        return '```json\n[{"name": "Hiro", "original": "Hiro"}]\n```'


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return make_clip(tmp_path_factory.mktemp("clips") / "c.mp4", DURATION, size="640x360")


def prepared(tmp_path: Path, clip: Path) -> Path:
    code, events = run_cli("new", "--root", str(tmp_path), "--title", "Thất Tình", "--source", str(clip))
    assert code == 0, events
    project_dir = Path(events[-1]["data"]["project"])
    assert run_cli("ingest", "--project", str(project_dir))[0] == 0
    beats = [{"id": f"B{i:04d}", "start": s, "end": s + 4.0, "shots": [[s, s + 4.0]], "keyframe": f"keyframes/B{i:04d}.jpg"}
             for i, s in enumerate(range(0, DURATION, 4), start=1)]  # fmt: skip
    (project_dir / "work/scenes.json").write_text(json.dumps(beats))
    lines = [{"id": 0, "start": 5.0, "end": 6.0, "text": "Hiro ơi!"}, {"id": 1, "start": 9.0, "end": 10.0, "text": "Đi thôi."}]
    (project_dir / "work/transcript.json").write_text(json.dumps(lines, ensure_ascii=False))
    project = config.load_project(project_dir)
    done = project.stages["scenes"].model_copy(update={"status": StageStatus.DONE})
    config.save_project(project_dir, project.with_stage("transcribe", done).with_stage("scenes", done))
    return project_dir


def run(project_dir: Path, reader: FakeReader, llm: FakeLLM) -> list[dict]:
    out = io.StringIO()
    stage = CharactersStage(reader_factory=lambda: reader, provider_factory=lambda settings: llm)
    ok = run_stage(project_dir, "characters", {"characters": stage}, Emitter(stream=out, raw_stream=io.StringIO()))
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert ok, events
    return events


@pytest.mark.integration
def test_people_their_pictures_and_the_names_said_are_listed(tmp_path: Path, clip: Path) -> None:
    # Arrange
    project_dir = prepared(tmp_path, clip)

    # Act
    run(project_dir, FakeReader(), FakeLLM())

    # Assert
    report = json.loads((project_dir / CHARACTERS).read_text(encoding="utf-8"))
    pan, hiro = report["characters"]
    assert (pan["id"], pan["faces"], hiro["id"], hiro["faces"]) == ("C01", DURATION, "C02", 4)
    assert hiro["beats"] == ["B0002"]
    assert len(pan["pictures"]) == 6 and len(hiro["pictures"]) == 4
    for picture in pan["pictures"] + hiro["pictures"]:
        assert f"work/{picture}".startswith(f"{PICTURES_DIR}/")
        with Image.open(project_dir / "work" / picture) as portrait:
            assert portrait.size == (240, 320)
    assert report["names"] == [{"name": "Hiro", "count": 1, "examples": ["Hiro ơi!"]}]
    assert not (project_dir / "work/characters/frames").exists()


@pytest.mark.integration
def test_new_beats_reuse_the_faces_and_the_names(tmp_path: Path, clip: Path) -> None:
    project_dir = prepared(tmp_path, clip)
    run(project_dir, FakeReader(), FakeLLM())
    beats = json.loads((project_dir / "work/scenes.json").read_text())
    (project_dir / "work/scenes.json").write_text(json.dumps(beats[:2]))

    reader, llm = FakeReader(), FakeLLM()
    events = run(project_dir, reader, llm)

    assert events[-1]["skipped"] is False and reader.calls == 0 and llm.calls == 0
    report = json.loads((project_dir / CHARACTERS).read_text(encoding="utf-8"))
    assert report["characters"][0]["beats"] == ["B0001", "B0002"]


@pytest.mark.integration
def test_the_users_naming_survives_a_rerun_with_the_same_faces(tmp_path: Path, clip: Path) -> None:
    project_dir = prepared(tmp_path, clip)
    run(project_dir, FakeReader(), FakeLLM())
    save_choices(project_dir, Choices(confirmed=True, names={"C01": "Pan", "C02": "Hiro"}))
    choices = (project_dir / "work/character_choices.json").read_bytes()
    (project_dir / PICTURES_DIR).rename(project_dir / "work/characters/legacy-pictures")
    project = config.load_project(project_dir)
    done = project.stages["characters"]
    config.save_project(project_dir, project.with_stage("describe", done).with_stage("script", done))

    reader, llm = FakeReader(), FakeLLM()
    events = run(project_dir, reader, llm)

    assert not events[-1]["skipped"]
    assert reader.calls == llm.calls == 0
    assert (project_dir / "work/character_choices.json").read_bytes() == choices
    assert load_choices(project_dir).names == {"C01": "Pan", "C02": "Hiro"}
    updated = config.load_project(project_dir)
    assert updated.stages["describe"] == updated.stages["script"] == done
