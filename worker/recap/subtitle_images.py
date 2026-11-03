"""Burned-in subtitles without libass: each cue is drawn to a transparent full-frame PNG with
Pillow, and an ffconcat list shows each image for its cue's time (blank between cues). The
export stage overlays that image stream on the video with ffmpeg's `overlay` filter, which
every ffmpeg build has (Homebrew's ffmpeg has neither `subtitles` nor `drawtext`).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from recap.subtitles import Cue

FONT_DIRS = (
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library" / "Fonts",
)
FONT_SUFFIXES = (".ttf", ".ttc", ".otf")
FONT_SCALE = 0.055  # font size relative to video height (≈59 px at 1080p)
OUTLINE_SCALE = 0.09  # outline width relative to font size
BOTTOM_MARGIN = 0.07  # distance of the last line from the bottom, relative to height
BANNER_GAP = 0.025  # distance of the last line above a channel banner, relative to height
LINE_SPACING = 0.15  # extra space between the two lines, relative to font size
TEXT_COLOR = (255, 255, 255, 255)
OUTLINE_COLOR = (0, 0, 0, 255)
BLANK = "blank.png"
TIME_DECIMALS = 3


class SubtitleError(Exception):
    pass


def resolve_font(name_or_path: str) -> Path:
    """A font file from a path, or from a name looked up in the macOS font folders."""
    direct = Path(name_or_path).expanduser()
    if direct.is_file():
        return direct
    for folder in FONT_DIRS:
        for suffix in FONT_SUFFIXES:
            candidate = folder / f"{name_or_path}{suffix}"
            if candidate.is_file():
                return candidate
    raise SubtitleError(f"Không tìm thấy font phụ đề {name_or_path!r} (đặt subtitle_font là tên font hoặc đường dẫn file)")


def render_cue(text: str, width: int, height: int, font_path: Path, out: Path, bottom: int = 0) -> None:
    """`bottom` = height of a channel banner at the bottom of the frame: the text sits just above it."""
    size = max(1, round(height * FONT_SCALE))
    font = ImageFont.truetype(str(font_path), size)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    baseline = height - bottom - height * (BANNER_GAP if bottom else BOTTOM_MARGIN)
    ImageDraw.Draw(image).multiline_text(
        (width / 2, baseline),
        text,
        font=font,
        fill=TEXT_COLOR,
        anchor="md",
        align="center",
        spacing=round(size * LINE_SPACING),
        stroke_width=max(1, round(size * OUTLINE_SCALE)),
        stroke_fill=OUTLINE_COLOR,
    )
    image.save(out)


def _quote(path: Path) -> str:
    return "'{}'".format(path.resolve().as_posix().replace("'", "'\\''"))


def overlay_concat(
    cues: Sequence[Cue], total_sec: float, width: int, height: int, font: Path, folder: Path, bottom: int = 0
) -> Path:
    """Write cue images and `subs.ffconcat` (image + duration, blank between cues) into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    blank = folder / BLANK
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(blank)

    shown: list[tuple[Path, float]] = []  # (image, start time)
    at = 0.0
    for n, cue in enumerate(cues, start=1):
        if cue.start > at:
            shown.append((blank, at))
        image = folder / f"cue_{n:04d}.png"
        render_cue(cue.text, width, height, font, image, bottom)
        shown.append((image, cue.start))
        at = cue.end
    shown.append((blank, at))

    # durations from rounded absolute times, so rounding never accumulates
    times = [round(start, TIME_DECIMALS) for _, start in shown] + [round(max(total_sec, at), TIME_DECIMALS)]
    lines = ["ffconcat version 1.0"]
    for (image, _), start, end in zip(shown, times, times[1:]):
        lines += [f"file {_quote(image)}", f"duration {end - start:.{TIME_DECIMALS}f}".rstrip("0").rstrip(".")]
    lines.append(f"file {_quote(shown[-1][0])}")  # concat demuxer ignores the last entry's duration
    listing = folder / "subs.ffconcat"
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listing
