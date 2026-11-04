"""Ads shown over the film (a betting site in a corner, a sponsor's hotline along the bottom):
blurred where and while they show, so the scene can still be used.

An ad whose text is large or near the middle of the frame is an ad card instead: it stays a flag
for `inspect_rules.skip_ranges`, and its beats are skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from recap.inspect_rules import MAX_FLAG_GAP_SEC, MIN_FLAG_RUN, Flag, Region, SkipRange

CARD_MIN_H = 0.08  # text at least this tall (share of the frame) is a card's headline
CENTER_BAND = 0.25  # text centred within this of the frame's centre, on both axes, is a card
AD_PAD = 0.02  # around the text: the logo or frame drawn with it


@dataclass(frozen=True)
class TimedBlur:
    start: float  # seconds in the source
    end: float
    region: Region
    text: str


def is_overlay_ad(flag: Flag) -> bool:
    if flag.kind != "ad" or flag.region is None:
        return False
    cx, cy = flag.region.x + flag.region.w / 2, flag.region.y + flag.region.h / 2
    centred = abs(cx - 0.5) < CENTER_BAND and abs(cy - 0.5) < CENTER_BAND
    return flag.region.h < CARD_MIN_H and not centred


def split_ads(flags: Sequence[Flag]) -> tuple[list[Flag], list[Flag]]:
    """(ads shown over the film, every other flag)."""
    overlay = [f for f in flags if is_overlay_ad(f)]
    return overlay, [f for f in flags if not is_overlay_ad(f)]


@dataclass(frozen=True)
class _Run:
    first: float
    last: float
    region: Region
    text: str
    count: int


def _grow(run: _Run, flag: Flag, region: Region) -> _Run:
    return _Run(run.first, flag.time, run.region.union(region), run.text, run.count + 1)


def ad_blurs(flags: Sequence[Flag], interval: float = 1.0) -> list[TimedBlur]:
    """One blur per ad seen in the same place in consecutive samples; an ad read in a single
    sample is ignored (like every other flag)."""
    gap = max(MAX_FLAG_GAP_SEC, interval * 2)
    runs: list[_Run] = []
    for flag in sorted(flags, key=lambda f: f.time):
        if flag.region is None:
            continue
        index = next((i for i, r in enumerate(runs) if flag.time - r.last <= gap and r.region.overlaps(flag.region)), None)
        if index is None:
            runs.append(_Run(flag.time, flag.time, flag.region, flag.text, 1))
        else:
            runs = [*runs[:index], _grow(runs[index], flag, flag.region), *runs[index + 1 :]]
    half = interval / 2
    return [TimedBlur(max(0.0, r.first - half), r.last + half, r.region.padded(AD_PAD), r.text)
            for r in runs if r.count >= MIN_FLAG_RUN]  # fmt: skip


def outside(blurs: Sequence[TimedBlur], skipped: Sequence[SkipRange]) -> list[TimedBlur]:
    """The blurs that matter: an ad entirely inside a skipped range never reaches the recap."""
    return [b for b in blurs if not any(r.start <= b.start and b.end <= r.end for r in skipped)]
