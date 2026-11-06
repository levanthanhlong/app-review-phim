"""Source cleanup when rendering, from what the `inspect` stage found (work/inspect.json):
the source's watermarks are blurred, ads shown over the film are blurred while they show, and
black bars are cropped while preserving the lower bar when it contains subtitles.

Regions are fractions of the source frame, so one filter works on the 480p proxy (preview) and
on the original film (export) alike: ffmpeg evaluates them with `iw`/`ih` and `main_w`/`main_h`.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from recap.ad_blurs import TimedBlur
from recap.inspect_rules import Region

CLEANUP_FILE = "work/inspect.json"
MIN_SIDE = 0.01  # regions thinner than this (after clamping) are noise, not a watermark
BLUR_POWER = 3  # boxblur passes: text in the region is unreadable afterwards


@dataclass(frozen=True)
class SourceCleanup:
    blurs: tuple[Region, ...] = ()
    crop: Region | None = None
    timed: tuple[TimedBlur, ...] = ()  # ads over the film, blurred only while they show
    keep_subtitle_bar: bool = False

    def key(self) -> str:
        """Stable text for fingerprints and render cache keys."""
        data = asdict(self)
        if not self.keep_subtitle_bar:
            del data["keep_subtitle_bar"]  # preserve existing cache keys
        return json.dumps(data, sort_keys=True)


def _num(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _clamped(raw: Any) -> Region | None:
    """The part of a {x, y, w, h} region inside the frame, or None when invalid or too small."""
    try:
        x, y, w, h = (float(raw[k]) for k in ("x", "y", "w", "h"))
    except (TypeError, KeyError, ValueError):
        return None
    left, top = max(0.0, x), max(0.0, y)
    right, bottom = min(1.0, x + w), min(1.0, y + h)
    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        return None
    return Region(round(left, 4), round(top, 4), round(right - left, 4), round(bottom - top, 4))


def load_cleanup(project_dir: Path) -> SourceCleanup:
    """What to clean in the source; no (or a damaged) report means nothing to clean."""
    try:
        report = json.loads((project_dir / CLEANUP_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return SourceCleanup()
    if not isinstance(report, dict):
        return SourceCleanup()
    marks = report.get("watermarks")
    blurs = tuple(r for r in map(_clamped, marks if isinstance(marks, list) else []) if r is not None)
    crop = _clamped(report.get("letterbox"))
    keep_subtitle_bar = False
    subtitles = report.get("subtitles")
    if crop is not None and isinstance(subtitles, list):
        bottom = round(crop.y + crop.h, 4)
        keep_subtitle_bar = any(band is not None and round(band.y + band.h, 4) > bottom
                                for band in map(_clamped, subtitles))
        if keep_subtitle_bar:
            crop = Region(0.0, crop.y, 1.0, round(1.0 - crop.y, 4)) if crop.y > 0 else None
    ads = report.get("ad_blurs")
    return SourceCleanup(
        blurs=blurs, crop=crop,
        timed=tuple(t for t in map(_timed, ads if isinstance(ads, list) else []) if t is not None),
        keep_subtitle_bar=keep_subtitle_bar,
    )


def _timed(raw: Any) -> TimedBlur | None:
    region = _clamped(raw)
    try:
        start, end, text = float(raw["start"]), float(raw["end"]), str(raw.get("text", ""))
    except (TypeError, KeyError, ValueError):
        return None
    return TimedBlur(start, end, region, text) if region is not None and end > start else None


def _blur(region: Region, label: str, enable: str = "") -> str:
    x, y, w, h = map(_num, (region.x, region.y, region.w, region.h))
    return (f"split=2[{label}m][{label}c];"
            f"[{label}c]crop=w=iw*{w}:h=ih*{h}:x=iw*{x}:y=ih*{y},"
            f"boxblur=luma_radius=min(w\\,h)/4:luma_power={BLUR_POWER}:"
            f"chroma_radius=min(cw\\,ch)/4:chroma_power={BLUR_POWER}[{label}b];"
            f"[{label}m][{label}b]overlay=x=main_w*{x}:y=main_h*{y}{enable}")  # fmt: skip


def cleanup_filter(cleanup: SourceCleanup, label: str, start: float = 0.0, end: float = math.inf) -> str:
    """Filter chain fragment (empty when nothing to clean) for the source's video from `start` to `end`
    (seconds in the source; the chain's timestamps start at 0 there, as after an input seek):
    blurs first (regions are measured on the full frame), then the crop. `label` keeps the
    fragment's pad names unique within one filter graph."""
    steps = [_blur(region, f"{label}w{i}") for i, region in enumerate(cleanup.blurs)]
    for i, ad in enumerate(t for t in cleanup.timed if t.start < end and t.end > start):
        enable = f":enable='between(t,{_num(max(0.0, ad.start - start))},{_num(ad.end - start)})'"
        steps.append(_blur(ad.region, f"{label}a{i}", enable))
    if cleanup.crop is not None:
        c = cleanup.crop
        steps.append(f"crop=w=iw*{_num(c.w)}:h=ih*{_num(c.h)}:x=iw*{_num(c.x)}:y=ih*{_num(c.y)}")
    return ",".join(steps)
