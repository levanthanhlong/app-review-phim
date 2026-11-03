"""Alignment of narration to footage (spec 4.7). Pure functions, no I/O.

For a `narrate` line the output length is TTS length + PAD_SEC. Clips are used in order:
  surplus  -> cut the tail ("trimmed" when more than TRIM_REPORT_SEC is dropped)
  shortage -> 1) extend the last clip forward to the end of its beat
              2) extend the first clip backward to the start of its beat
              3) slow the footage down, not below MIN_SPEED
              4) still short -> "short" (the renderer holds the last frame)
An `original` line keeps its footage and sound as-is.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from recap.models import Beat
from recap.script_model import PAD_SEC, parse_tc

MIN_SPEED = 0.85
TRIM_REPORT_SEC = 1.0
EPS = 1e-6
DECIMALS = 3

Fit = Literal["ok", "short", "trimmed"]


@dataclass(frozen=True)
class ClipRef:
    beat: str
    start: float
    end: float


@dataclass(frozen=True)
class Segment:
    src_start: float
    src_end: float
    speed: float = 1.0

    @property
    def out_duration(self) -> float:
        return (self.src_end - self.src_start) / self.speed


@dataclass(frozen=True)
class Fitted:
    segments: list[Segment]
    fit: Fit


@dataclass(frozen=True)
class TimelineEntry:
    id: str
    type: Literal["narrate", "original"]
    out_start: float
    out_end: float
    segments: list[Segment] = field(default_factory=list)
    tts_file: str | None = None
    tts_dur: float | None = None
    fit: Fit = "ok"


def _r(value: float) -> float:
    return round(value, DECIMALS)


def _take(spans: list[tuple[float, float]], needed: float) -> list[tuple[float, float]]:
    """Keep spans in order until `needed` seconds are covered; the last one is cut short."""
    kept: list[tuple[float, float]] = []
    remaining = needed
    for start, end in spans:
        if remaining <= EPS:
            break
        length = min(end - start, remaining)
        kept.append((start, start + length))
        remaining -= length
    return kept


def _extend(spans: list[tuple[float, float]], beats: list[Beat], shortage: float) -> list[tuple[float, float]]:
    (last_start, last_end), last_beat = spans[-1], beats[-1]
    new_end = min(last_beat.end, last_end + shortage)
    spans = [*spans[:-1], (last_start, new_end)]
    shortage -= new_end - last_end
    if shortage > EPS:
        (first_start, first_end), first_beat = spans[0], beats[0]
        new_start = max(first_beat.start, first_start - shortage)
        spans = [(new_start, first_end), *spans[1:]]
    return spans


def fit_narration(clips: Sequence[ClipRef], beats: Mapping[str, Beat], needed: float) -> Fitted:
    clip_beats = [beats[c.beat] for c in clips]  # KeyError for unknown beats
    spans = [(c.start, c.end) for c in clips]
    total = sum(e - s for s, e in spans)

    if total >= needed - EPS:
        kept = _take(spans, needed)
        fit: Fit = "trimmed" if total - needed > TRIM_REPORT_SEC else "ok"
        return Fitted([Segment(_r(s), _r(e)) for s, e in kept], fit)

    spans = _extend(spans, clip_beats, needed - total)
    total = sum(e - s for s, e in spans)
    if total >= needed - EPS:
        return Fitted([Segment(_r(s), _r(e)) for s, e in _take(spans, needed)], "ok")

    speed = max(MIN_SPEED, total / needed)
    fit = "ok" if total / needed >= MIN_SPEED - EPS else "short"
    return Fitted([Segment(_r(s), _r(e), speed) for s, e in spans], fit)


def build_timeline(
    items: Sequence[Mapping[str, Any]],
    beats: Mapping[str, Beat],
    tts: Mapping[str, tuple[str, float]],
    pad: float = PAD_SEC,
) -> list[TimelineEntry]:
    """Place script items back to back. `tts` maps line id -> (file, duration)."""
    entries: list[TimelineEntry] = []
    cursor = 0.0
    for item in items:
        if item["type"] == "narrate":
            tts_file, tts_dur = tts[item["id"]]  # KeyError when a narration has no audio
            clips = [ClipRef(c["beat"], parse_tc(c["start"]), parse_tc(c["end"])) for c in item["clips"]]
            fitted = fit_narration(clips, beats, tts_dur + pad)
            duration = tts_dur + pad
            entry = TimelineEntry(item["id"], "narrate", 0.0, 0.0, fitted.segments, tts_file, tts_dur, fitted.fit)
        else:
            start, end = parse_tc(item["start"]), parse_tc(item["end"])
            duration = end - start
            entry = TimelineEntry(item["id"], "original", 0.0, 0.0, [Segment(_r(start), _r(end))])
        entries.append(replace(entry, out_start=_r(cursor), out_end=_r(cursor + duration)))
        cursor += duration
    return entries


def timeline_from_json(data: Sequence[Mapping[str, Any]]) -> list[TimelineEntry]:
    """Inverse of `[asdict(entry) for entry in timeline]` (work/timeline.json)."""
    return [
        TimelineEntry(
            id=str(e["id"]),
            type=e["type"],
            out_start=float(e["out_start"]),
            out_end=float(e["out_end"]),
            segments=[Segment(float(s["src_start"]), float(s["src_end"]), float(s.get("speed", 1.0))) for s in e["segments"]],
            tts_file=e.get("tts_file"),
            tts_dur=e.get("tts_dur"),
            fit=e.get("fit", "ok"),
        )
        for e in data
    ]
