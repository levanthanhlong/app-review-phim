"""Assemble stage end-to-end on a synthetic clip with fake TTS audio."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from recap import config, media
from recap.branding import BrandStyle, brand_layout
from recap.cleanup import SourceCleanup
from recap.fit import Segment, TimelineEntry
from recap.inspect_rules import Region
from recap.models import StageStatus
from recap.render import PREVIEW, duck_expression, item_args
from tests.conftest import HAS_FFMPEG, make_clip
from tests.test_cli import WORKER_DIR, new_project, run_cli

BEATS = [
    {"id": "B0001", "start": 0.0, "end": 10.0, "shots": [[0.0, 10.0]], "keyframe": "keyframes/B0001.jpg"},
    {"id": "B0002", "start": 10.0, "end": 20.0, "shots": [[10.0, 20.0]], "keyframe": "keyframes/B0002.jpg"},
    {"id": "B0003", "start": 20.0, "end": 30.0, "shots": [[20.0, 30.0]], "keyframe": "keyframes/B0003.jpg"},
]
SCRIPT = """title: Test
target_duration_sec: 12
items:
- id: L0001
  type: narrate
  text: Câu một.
  clips:
  - {beat: B0001, start: "00:00:01", end: "00:00:03"}
  - {beat: B0002, start: "00:00:11", end: "00:00:13"}
- id: L0002
  type: original
  beat: B0003
  start: "00:00:21"
  end: "00:00:25"
- id: L0003
  type: narrate
  text: Câu hai.
  clips:
  - {beat: B0002, start: "00:00:15", end: "00:00:17"}
"""


def tone(path: Path, seconds: float, freq: int = 600) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "44100", "-ac", "1", str(path)], check=True)  # fmt: skip


def write_manifest(project_dir: Path, entries: list[tuple[str, str, float]]) -> None:
    tts_dir = project_dir / "work/tts"
    tts_dir.mkdir(exist_ok=True)
    data = []
    for line_id, name, seconds in entries:
        tone(tts_dir / name, seconds)
        data.append({"id": line_id, "file": f"tts/{name}", "duration": seconds, "engine": "edge"})
    (tts_dir / "manifest.json").write_text(json.dumps(data))


@pytest.fixture(scope="module")
def clip_30s(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return make_clip(tmp_path_factory.mktemp("clips") / "c30.mp4", 30, size="1280x720")


def prepared_project(tmp_path: Path, clip: Path, title: str = "Clip") -> Path:
    project_dir = new_project(tmp_path, clip, title)
    assert run_cli("ingest", "--project", str(project_dir))[0] == 0
    (project_dir / "work/scenes.json").write_text(json.dumps(BEATS))
    (project_dir / "script.yaml").write_text(SCRIPT)
    # L0001 needs 5.75 + 0.25 = 6 s (4 s of clips -> extended); L0003 needs 2.75 + 0.25 = 3 s
    write_manifest(project_dir, [("L0001", "a.wav", 5.75), ("L0003", "b.wav", 2.75)])
    project = config.load_project(project_dir)
    done = project.stages["tts"].model_copy(update={"status": StageStatus.DONE})
    stages = {n: (done if n not in ("assemble", "export") else s) for n, s in project.stages.items()}
    config.save_project(project_dir, project.model_copy(update={"stages": stages}))
    return project_dir


def logs(events: list[dict]) -> str:
    return "\n".join(e["message"] for e in events if e["type"] == "log")


def test_duck_expression_fades_only_at_requested_edges() -> None:
    assert duck_expression(5.0, 0.15, fade_in=False, fade_out=False) == "0.15"
    expr = duck_expression(5.0, 0.15, fade_in=True, fade_out=True)
    assert "lt(t,0.2)" in expr and "gt(t,4.8)" in expr


def test_item_args_take_the_chosen_audio_stream() -> None:
    entry = TimelineEntry("L0001", "original", 0.0, 2.0, [Segment(1.0, 2.0), Segment(5.0, 6.0)])

    default = " ".join(item_args(entry, Path("s.mp4"), None, PREVIEW, 25, 0.15, True, True, Path("o.mkv")))
    second = " ".join(item_args(entry, Path("s.mp4"), None, PREVIEW, 25, 0.15, True, True, Path("o.mkv"), 1))

    assert "[0:a:0]" in default and "[1:a:0]" in default
    assert "[0:a:1]" in second and "[1:a:1]" in second


@pytest.mark.integration
def test_assemble_renders_preview_matching_timeline(tmp_path: Path, clip_30s: Path) -> None:
    # Arrange
    project_dir = prepared_project(tmp_path, clip_30s)

    # Act
    code, events = run_cli("assemble", "--project", str(project_dir))

    # Assert: timeline
    assert code == 0, events
    timeline = json.loads((project_dir / "work/timeline.json").read_text())
    assert [t["id"] for t in timeline] == ["L0001", "L0002", "L0003"]
    assert timeline[0]["out_end"] == pytest.approx(6.0) and timeline[0]["fit"] == "ok"
    assert timeline[-1]["out_end"] == pytest.approx(6.0 + 4.0 + 3.0)
    # Assert: video
    out = project_dir / "output/recap_preview.mp4"
    info = media.probe(out)
    assert (info.width, info.height) == (854, 480)
    assert info.duration == pytest.approx(13.0, abs=0.2)
    assert len(info.audio_tracks) == 1
    assert "fit: ok=3 short=0 trimmed=0" in logs(events)
    assert {"work/timeline.json", "output/recap_preview.mp4"} <= {e["path"] for e in events if e["type"] == "artifact"}


@pytest.mark.integration
def test_relative_project_path_and_quote_in_title(tmp_path: Path, clip_30s: Path) -> None:
    project_dir = prepared_project(tmp_path, clip_30s, title="Phim's Recap")
    relative = os.path.relpath(project_dir, WORKER_DIR)  # run_cli runs from WORKER_DIR

    code, events = run_cli("assemble", "--project", relative)

    assert code == 0, events
    assert media.probe(project_dir / "output/recap_preview.mp4").duration == pytest.approx(13.0, abs=0.2)


@pytest.mark.integration
def test_changing_one_tts_line_rerenders_only_that_line(tmp_path: Path, clip_30s: Path) -> None:
    project_dir = prepared_project(tmp_path, clip_30s)
    run_cli("assemble", "--project", str(project_dir))

    _, events = run_cli("assemble", "--project", str(project_dir))
    assert events[-1]["skipped"] is True

    write_manifest(project_dir, [("L0001", "a.wav", 5.75), ("L0003", "c.wav", 3.75)])
    code, events = run_cli("assemble", "--project", str(project_dir))

    assert code == 0
    assert "Render: 1 dòng mới, 2 dòng dùng cache" in logs(events)
    assert media.probe(project_dir / "output/recap_preview.mp4").duration == pytest.approx(14.0, abs=0.2)
    assert len(list((project_dir / "work/render/preview").glob("*.mkv"))) == 3  # stale clip removed


@pytest.mark.integration
def test_missing_tts_file_fails_clearly(tmp_path: Path, clip_30s: Path) -> None:
    project_dir = prepared_project(tmp_path, clip_30s)
    (project_dir / "work/tts/b.wav").unlink()

    code, events = run_cli("assemble", "--project", str(project_dir))

    assert code != 0
    assert next(e for e in events if e["type"] == "error")["code"] == "input_missing"


def test_join_normalizes_loudness_to_minus_14_lufs() -> None:
    from recap.render import concat_args

    args = concat_args(Path("list.txt"), Path("out.mp4"))

    assert "loudnorm=I=-14" in args[args.index("-af") + 1]


def test_item_args_clean_every_segment_before_scaling() -> None:
    entry = TimelineEntry("L0001", "original", 0.0, 2.0, [Segment(1.0, 2.0), Segment(5.0, 6.0)])
    cleanup = SourceCleanup(blurs=(Region(0.05, 0.8, 0.3, 0.2),), crop=Region(0.0, 0.125, 1.0, 0.75))

    graph = item_args(entry, Path("s.mp4"), None, PREVIEW, 25, 0.0, True, True, Path("o.mkv"), cleanup=cleanup)
    graph = graph[graph.index("-filter_complex") + 1]

    for i in (0, 1):  # each segment has its own (uniquely named) cleanup, before the scale
        chain = graph[graph.index(f"[{i}:v]"):graph.index(f"[v{i}]")]
        assert "boxblur" in chain and "crop=w=iw*1:h=ih*0.75" in chain
        assert chain.index("crop=w=iw*1:h=ih*0.75") < chain.index("scale=")
    assert graph.count("split=2") == 2


@pytest.mark.parametrize("overlay", [None, False, True])
def test_render_fits_protected_subtitles_above_the_banner(overlay: bool | None) -> None:
    entry = TimelineEntry("L0001", "original", 0.0, 2.0, [Segment(1.0, 3.0)])
    cleanup = SourceCleanup(crop=Region(0.0, 0.125, 1.0, 0.875), keep_subtitle_bar=True)
    brand = None
    if overlay is not None:
        layout = brand_layout((600, 50), PREVIEW.width, PREVIEW.height, BrandStyle(overlay=overlay))
        brand = (layout, Path("brand.png"))
    args = item_args(entry, Path("s.mp4"), None, PREVIEW, 25, 0.0, True, True, Path("o.mkv"),
                     brand=brand, cleanup=cleanup)
    graph = args[args.index("-filter_complex") + 1]

    assert "crop=w=iw*1:h=ih*0.875:x=iw*0:y=ih*0.125" in graph
    fit_height = PREVIEW.height if brand is None else PREVIEW.height - layout.top - layout.bottom
    assert f"scale={PREVIEW.width}:{fit_height}:force_original_aspect_ratio=decrease" in graph
    assert "force_original_aspect_ratio=increase" not in graph


@pytest.mark.integration
def test_assemble_rerenders_when_the_inspect_report_changes_the_cleanup(tmp_path: Path, clip_30s: Path) -> None:
    # Arrange
    project_dir = prepared_project(tmp_path, clip_30s)
    assert run_cli("assemble", "--project", str(project_dir))[0] == 0
    report = {"watermarks": [{"x": 0.05, "y": 0.8, "w": 0.3, "h": 0.15, "text": "kenh"}], "letterbox": None,
              "skip_beats": {}}  # fmt: skip
    (project_dir / "work/inspect.json").write_text(json.dumps(report))

    # Act
    code, events = run_cli("assemble", "--project", str(project_dir))

    # Assert
    assert code == 0, events
    assert events[-1]["skipped"] is False
    assert "3 dòng mới" in logs(events)
