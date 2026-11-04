"""Source cleanup when rendering: watermarks blurred and black bars cropped, from work/inspect.json."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from recap.ad_blurs import TimedBlur
from recap.cleanup import CLEANUP_FILE, SourceCleanup, cleanup_filter, load_cleanup
from recap.inspect_rules import Region
from tests.conftest import HAS_FFMPEG


def write_report(project_dir: Path, report: dict) -> None:
    (project_dir / "work").mkdir(exist_ok=True)
    (project_dir / CLEANUP_FILE).write_text(json.dumps(report), encoding="utf-8")


def test_reads_watermarks_and_letterbox_from_the_inspect_report(tmp_path: Path) -> None:
    write_report(tmp_path, {"watermarks": [{"x": 0.05, "y": 0.8, "w": 0.3, "h": 0.2, "text": "FAPtv"}],
                            "letterbox": {"x": 0.0, "y": 0.125, "w": 1.0, "h": 0.75}})  # fmt: skip

    cleanup = load_cleanup(tmp_path)

    assert cleanup == SourceCleanup(blurs=(Region(0.05, 0.8, 0.3, 0.2),), crop=Region(0.0, 0.125, 1.0, 0.75))


@pytest.mark.parametrize("content", [None, "{broken", json.dumps({"watermarks": "nope"})])
def test_no_or_damaged_report_means_no_cleanup(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / "work").mkdir()
        (tmp_path / CLEANUP_FILE).write_text(content, encoding="utf-8")

    assert load_cleanup(tmp_path) == SourceCleanup()
    assert cleanup_filter(SourceCleanup(), "s0") == ""


def test_regions_are_clamped_inside_the_frame(tmp_path: Path) -> None:
    write_report(tmp_path, {"watermarks": [{"x": -0.02, "y": 0.9, "w": 0.3, "h": 0.2}], "letterbox": None})

    assert load_cleanup(tmp_path).blurs == (Region(0.0, 0.9, 0.28, 0.1),)


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not available")
def test_filter_blurs_the_region_and_crops_the_bars_at_any_resolution(tmp_path: Path) -> None:
    # Arrange: a sharp checkerboard; blur its bottom-left corner and crop 1/8 off top and bottom
    cleanup = SourceCleanup(blurs=(Region(0.0, 0.5, 0.5, 0.5),), crop=Region(0.0, 0.125, 1.0, 0.75))
    out = tmp_path / "frame.png"
    chain = cleanup_filter(cleanup, "c")

    # Act
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:size=640x480,format=gray",
                    "-f", "lavfi", "-i", "color=black:size=640x480,format=gray",
                    "-filter_complex", f"[0:v][1:v]blend=all_expr='if(mod(floor(X/8)+floor(Y/8),2),A,B)'[base];"
                                       f"[base]{chain}[v]",
                    "-map", "[v]", "-frames:v", "1", str(out)], check=True)  # fmt: skip

    # Assert
    pixels = np.asarray(Image.open(out).convert("L"), dtype=np.float32)
    assert pixels.shape == (360, 640)
    blurred, sharp = pixels[200:340, 20:300], pixels[20:160, 340:620]
    assert sharp.std() > 100 and blurred.std() < 30


AD = TimedBlur(start=11.0, end=12.0, region=Region(0.0, 0.0, 1.0, 1.0), text="w88.bet")


def test_ad_blurs_are_read_with_their_times(tmp_path: Path) -> None:
    write_report(tmp_path, {"watermarks": [], "letterbox": None,
                            "ad_blurs": [{"start": 11.0, "end": 12.0, "x": 0, "y": 0, "w": 1, "h": 1, "text": "w88.bet"}]})  # fmt: skip

    assert load_cleanup(tmp_path).timed == (AD,)


def test_a_segment_gets_only_the_ad_blurs_it_shows_with_times_from_its_start() -> None:
    cleanup = SourceCleanup(timed=(AD,))

    assert "enable='between(t,1,2)'" in cleanup_filter(cleanup, "s0", start=10.0, end=15.0)
    assert cleanup_filter(cleanup, "s0", start=20.0, end=25.0) == ""


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not available")
def test_ad_blur_starts_and_stops_on_time_in_a_seeked_segment(tmp_path: Path) -> None:
    # Arrange: a 20 s checkerboard; the segment starts at 10 s (input seek, as when rendering)
    clip = tmp_path / "board.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:size=320x240:duration=20",
                    "-f", "lavfi", "-i", "color=black:size=320x240:duration=20",
                    "-filter_complex", "[0:v][1:v]blend=all_expr='if(mod(floor(X/8)+floor(Y/8),2),A,B)',format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", str(clip)], check=True)  # fmt: skip
    chain = cleanup_filter(SourceCleanup(timed=(AD,)), "s0", start=10.0, end=14.0)

    def frame_std(at: float) -> float:
        out = tmp_path / f"f{at}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "10", "-t", "4", "-i", str(clip),
                        "-filter_complex", f"[0:v]{chain},trim=start={at},setpts=PTS-STARTPTS[v]",
                        "-map", "[v]", "-frames:v", "1", str(out)], check=True)  # fmt: skip
        return float(np.asarray(Image.open(out).convert("L"), dtype=np.float32).std())

    # Act / Assert: source 10.5 s sharp, 11.5 s blurred, 12.5 s sharp again
    assert frame_std(0.5) > 100 and frame_std(1.5) < 30 and frame_std(2.5) > 100
