"""Burned-in subtitles: cue images (Pillow) and the ffconcat list that times them."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from recap.subtitle_images import (
    SubtitleError,
    overlay_concat,
    render_cue,
    resolve_font,
)
from recap.subtitles import Cue

try:
    FONT: Path | None = resolve_font("Arial")
except SubtitleError:
    FONT = None
needs_font = pytest.mark.skipif(FONT is None, reason="Arial not installed")


@needs_font
def test_resolve_font_by_name_and_by_path() -> None:
    assert FONT is not None and FONT.is_file()
    assert resolve_font(str(FONT)) == FONT


def test_missing_font_is_reported() -> None:
    with pytest.raises(SubtitleError, match="font"):
        resolve_font("Không Có Font Này")


@needs_font
def test_cue_image_is_transparent_frame_with_text_at_the_bottom(tmp_path: Path) -> None:
    out = tmp_path / "c.png"

    render_cue("Trời đang mưa, cóc kiện trời\nỞ dòng thứ hai", 640, 360, FONT, out)

    img = Image.open(out)
    assert img.size == (640, 360) and img.mode == "RGBA"
    alpha = img.getchannel("A")
    assert alpha.crop((0, 0, 640, 180)).getbbox() is None  # top half fully transparent
    box = alpha.getbbox()
    assert box is not None and box[3] <= 360 and box[1] > 200
    colors = {img.getpixel((x, y))[:3] for x in range(box[0], box[2], 3) for y in range(box[1], box[3], 3)
              if img.getpixel((x, y))[3] == 255}  # fmt: skip
    assert (255, 255, 255) in colors and (0, 0, 0) in colors  # white text, black outline


@needs_font
def test_overlay_concat_times_every_cue_with_blank_gaps(tmp_path: Path) -> None:
    cues = [Cue(0.1, 2.1, "Một"), Cue(2.1, 3.0, "Hai"), Cue(5.0, 6.5, "Ba")]

    listing = overlay_concat(cues, total_sec=8.0, width=320, height=180, font=FONT, folder=tmp_path)

    lines = listing.read_text().splitlines()
    assert lines[0] == "ffconcat version 1.0"
    entries = [(lines[i], lines[i + 1]) for i in range(1, len(lines) - 1, 2)]
    files = [f.split("'")[1] for f, _ in entries]
    durations = [float(d.split()[1]) for _, d in entries]
    assert [Path(f).name for f in files] == ["blank.png", "cue_0001.png", "cue_0002.png", "blank.png", "cue_0003.png", "blank.png"]
    assert durations == pytest.approx([0.1, 2.0, 0.9, 2.0, 1.5, 1.5])
    assert lines[-1] == entries[-1][0]  # the last file is repeated so its duration is honoured
    assert all(Path(f).is_file() for f in files)


@needs_font
def test_overlay_concat_without_cues_is_one_blank(tmp_path: Path) -> None:
    listing = overlay_concat([], total_sec=3.0, width=320, height=180, font=FONT, folder=tmp_path)

    assert "duration 3" in listing.read_text()


@needs_font
def test_cue_sits_above_the_bottom_banner(tmp_path: Path) -> None:
    # Arrange: a 1080p frame whose bottom 230 px are the channel banner
    out = tmp_path / "cue.png"

    # Act
    render_cue("Câu kể nằm trên banner", 1920, 1080, FONT, out, bottom=230)  # type: ignore[arg-type]

    # Assert: every drawn pixel is above the banner, and close to it
    alpha = Image.open(out).getchannel("A")
    box = alpha.getbbox()
    assert box is not None and box[3] <= 1080 - 230
    assert box[3] > 1080 - 230 - 120
