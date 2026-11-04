"""Stage `export`: SRT, final render from the original film, burned-in subtitles."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from recap import config, media
from recap.models import MediaInfo
from recap.stages.export import final_profile
from tests.test_assemble import clip_30s, prepared_project  # noqa: F401  (fixture re-export)
from tests.test_cli import run_cli


def frame(video: Path, at: float, out: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(out)], check=True)
    return Image.open(out).convert("RGB")


def exported_project(tmp_path: Path, clip: Path) -> Path:
    project_dir = prepared_project(tmp_path, clip)
    code, events = run_cli("assemble", "--project", str(project_dir))
    assert code == 0, events
    return project_dir


@pytest.mark.parametrize("height", [480, 720, 1080, 2160, None])
def test_final_profile_is_always_1080p_at_a_fixed_bitrate(height: int | None) -> None:
    # smaller sources are scaled up (lanczos + light sharpening): the platforms favour 1080p uploads
    profile = final_profile(MediaInfo(duration=1, width=None if height is None else height * 2, height=height))

    assert (profile.width, profile.height) == (1920, 1080) and profile.name == "final"
    assert profile.video_bitrate == "10M" and profile.sharpen


def test_final_render_uses_the_bitrate_and_sharpens() -> None:
    from recap.fit import Segment, TimelineEntry
    from recap.render import item_args

    entry = TimelineEntry("L1", "original", 0.0, 4.0, [Segment(10.0, 14.0)])
    args = item_args(entry, Path("src.mp4"), None, final_profile(None), 25.0, 0.0, True, True, Path("o.mkv"))

    graph = args[args.index("-filter_complex") + 1]
    assert "unsharp=" in graph
    assert args[args.index("-b:v") + 1] == "10M" and args[args.index("-maxrate") + 1] == "12M"
    assert "-crf" not in args


@pytest.mark.integration
def test_export_writes_srt_final_and_subbed(tmp_path: Path, clip_30s: Path) -> None:  # noqa: F811
    # Arrange
    project_dir = exported_project(tmp_path, clip_30s)

    # Act
    code, events = run_cli("export", "--project", str(project_dir))

    # Assert: files
    assert code == 0, events
    out = project_dir / "output"
    artifacts = {e["path"] for e in events if e["type"] == "artifact"}
    assert {"output/recap.srt", "output/recap.mp4", "output/recap_subbed.mp4"} <= artifacts
    # SRT: L0001 narration starts at 0.1 s
    srt = (out / "recap.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,100 --> ")
    # final: from the 1280x720 source, scaled up to 1080p; same length as the preview
    final = media.probe(out / "recap.mp4")
    assert (final.width, final.height) == (1920, 1080)
    assert final.duration == pytest.approx(13.0, abs=0.2)
    subbed = media.probe(out / "recap_subbed.mp4")
    assert (subbed.width, subbed.height) == (1920, 1080)
    assert subbed.duration == pytest.approx(final.duration, abs=0.1)
    # subtitles are burned in during narration (t=1 s) and absent during the original clip (t=8 s)
    bottom = (0, 840, 1920, 1080)
    narr_diff = ImageChops.difference(frame(out / "recap.mp4", 1.0, tmp_path / "a.png"),
                                      frame(out / "recap_subbed.mp4", 1.0, tmp_path / "b.png"))  # fmt: skip
    orig_diff = ImageChops.difference(frame(out / "recap.mp4", 8.0, tmp_path / "c.png"),
                                      frame(out / "recap_subbed.mp4", 8.0, tmp_path / "d.png"))  # fmt: skip
    assert max(narr_diff.crop(bottom).convert("L").getextrema()) > 100
    assert max(orig_diff.crop(bottom).convert("L").getextrema()) < 40


@pytest.mark.integration
def test_export_second_run_is_skipped(tmp_path: Path, clip_30s: Path) -> None:  # noqa: F811
    project_dir = exported_project(tmp_path, clip_30s)
    run_cli("export", "--project", str(project_dir))

    _, events = run_cli("export", "--project", str(project_dir))

    assert events[-1]["skipped"] is True


@pytest.mark.integration
def test_unknown_subtitle_font_fails_before_rendering(tmp_path: Path, clip_30s: Path) -> None:  # noqa: F811
    project_dir = exported_project(tmp_path, clip_30s)
    project = config.load_project(project_dir)
    settings = project.settings.model_copy(update={"subtitle_font": "Không Có Font Này"})
    config.save_project(project_dir, project.model_copy(update={"settings": settings}))

    code, events = run_cli("export", "--project", str(project_dir))

    assert code == 1 and next(e for e in events if e["type"] == "error")["code"] == "font_missing"
    assert not (project_dir / "work/render/final").exists()
