"""Scenes stage end-to-end on a synthetic clip with hard cuts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from recap import config, media
from recap.models import StageStatus
from tests.conftest import HAS_FFMPEG
from tests.test_cli import new_project, run_cli

# Hard cuts at 6, 12, 15, 17, 19 s; then a 30 s single shot (must be split into ≤20 s beats).
SEGMENTS = [("red", 6), ("blue", 6), ("green", 3), ("yellow", 2), ("white", 2), ("testsrc2", 30)]


@pytest.fixture(scope="module")
def cut_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    path = tmp_path_factory.mktemp("clips") / "cuts.mp4"
    inputs: list[str] = []
    for src, dur in SEGMENTS:
        lavfi = f"testsrc2=size=640x360:rate=25:d={dur}" if src == "testsrc2" else f"color=c={src}:s=640x360:r=25:d={dur}"
        inputs += ["-f", "lavfi", "-i", lavfi]
    total = sum(d for _, d in SEGMENTS)
    inputs += ["-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=48000:duration={total}"]
    n = len(SEGMENTS)
    concat = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", concat, "-map", "[v]", "-map", f"{n}:a",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]  # fmt: skip
    subprocess.run(cmd, check=True)
    return path


def _with_fake_transcript(project_dir: Path) -> None:
    transcript = [
        {"id": 1, "start": 1.0, "end": 3.0, "text": "Màu đỏ", "speaker": None, "words": []},
        {"id": 2, "start": 13.0, "end": 14.0, "text": "Màu xanh lá", "speaker": None, "words": []},
    ]
    (project_dir / "work/transcript.json").write_text(json.dumps(transcript, ensure_ascii=False))
    project = config.load_project(project_dir)
    done = project.stages["transcribe"].model_copy(update={"status": StageStatus.DONE})
    config.save_project(project_dir, project.with_stage("transcribe", done))


@pytest.mark.integration
def test_scenes_builds_beats_and_keyframes(tmp_path: Path, cut_clip: Path) -> None:
    # Arrange
    project_dir = new_project(tmp_path, cut_clip)
    assert run_cli("ingest", "--project", str(project_dir))[0] == 0
    _with_fake_transcript(project_dir)

    # Act
    code, evs = run_cli("scenes", "--project", str(project_dir))

    # Assert
    assert code == 0, evs
    beats = json.loads((project_dir / "work/scenes.json").read_text())
    assert [b["id"] for b in beats[:2]] == ["B0001", "B0002"]
    assert beats[0]["start"] == 0.0 and beats[-1]["end"] == pytest.approx(49.0, abs=0.1)
    assert all(a["end"] == b["start"] for a, b in zip(beats, beats[1:]))
    assert all(b["end"] - b["start"] <= 20.0 + 1e-6 for b in beats)
    # first real cut detected near 6 s
    assert beats[0]["shots"][0][1] == pytest.approx(6.0, abs=0.1)
    assert beats[0]["dialogue"] == "Màu đỏ"
    for b in beats:
        frame = project_dir / "work" / b["keyframe"]
        assert frame.is_file()
        assert media.probe_image_width(frame) == 512
    assert any(e["type"] == "progress" for e in evs)
    assert not (project_dir / "work/keyframes.tmp").exists()


@pytest.mark.integration
def test_scenes_skips_when_up_to_date_and_reruns_after_setting_change(tmp_path: Path, cut_clip: Path) -> None:
    project_dir = new_project(tmp_path, cut_clip)
    run_cli("ingest", "--project", str(project_dir))
    _with_fake_transcript(project_dir)
    run_cli("scenes", "--project", str(project_dir))

    _, evs = run_cli("scenes", "--project", str(project_dir))
    assert evs[-1]["skipped"] is True

    project = config.load_project(project_dir)
    config.save_project(
        project_dir, project.model_copy(update={"settings": project.settings.model_copy(update={"max_beat_sec": 10})})
    )
    _, evs = run_cli("scenes", "--project", str(project_dir))
    assert evs[-1]["skipped"] is False
    beats = json.loads((project_dir / "work/scenes.json").read_text())
    assert all(b["end"] - b["start"] <= 10.0 + 1e-6 for b in beats)
    # old keyframes from the previous run are gone
    names = {p.name for p in (project_dir / "work/keyframes").iterdir()}
    assert names == {Path(b["keyframe"]).name for b in beats}
