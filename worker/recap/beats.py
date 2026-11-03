"""Pure logic for grouping shots into beats (no I/O).

A beat is a run of consecutive shots, at least `min_sec` and at most `max_sec` long.
Cuts are preferred at shot boundaries where nobody is talking. Shots longer than
`max_sec` are first split into pieces, also preferring silent points.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from recap.models import TranscriptSegment

Interval = tuple[float, float]

# Gaps in speech shorter than this are treated as continuous talking.
SPEECH_GAP_MERGE_SEC = 0.3


@dataclass(frozen=True)
class BeatPlan:
    start: float
    end: float
    shots: list[Interval] = field(default_factory=list)


# ---- speech ------------------------------------------------------------------

def speech_intervals(segments: Sequence[TranscriptSegment]) -> list[Interval]:
    """Sorted, merged intervals where someone is talking (word-level when available)."""
    raw: list[Interval] = []
    for seg in segments:
        if seg.words:
            raw.extend((w.start, w.end) for w in seg.words)
        else:
            raw.append((seg.start, seg.end))
    merged: list[Interval] = []
    for start, end in sorted(raw):
        if merged and start - merged[-1][1] < SPEECH_GAP_MERGE_SEC:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def in_speech(t: float, speech: Sequence[Interval]) -> bool:
    """True if `t` falls strictly inside a speech interval (edges count as silence)."""
    return any(start < t < end for start, end in speech)


# ---- splitting long shots ------------------------------------------------------

def _best_cut(lo: float, hi: float, ideal: float, speech: Sequence[Interval]) -> float:
    """Silent point in [lo, hi] closest to `ideal`; `ideal` itself if no silent point exists."""
    candidates = [ideal] + [edge for interval in speech for edge in interval]
    silent = [c for c in candidates if lo <= c <= hi and not in_speech(c, speech)]
    return min(silent, key=lambda c: abs(c - ideal)) if silent else ideal


def split_long_shot(shot: Interval, speech: Sequence[Interval], min_sec: float, max_sec: float) -> list[Interval]:
    start, end = shot
    pieces: list[Interval] = []
    while end - start > max_sec:
        remaining = end - start
        ideal = start + remaining / math.ceil(remaining / max_sec)
        cut = _best_cut(start + min_sec, min(start + max_sec, end - min_sec), ideal, speech)
        pieces.append((start, cut))
        start = cut
    pieces.append((start, end))
    return pieces


# ---- merging into beats --------------------------------------------------------

def _validate_limits(min_sec: float, max_sec: float) -> None:
    if min_sec <= 0 or max_sec < min_sec:
        raise ValueError(f"Giới hạn beat không hợp lệ: min={min_sec}, max={max_sec}")


def _length(shots: Sequence[Interval]) -> float:
    return shots[-1][1] - shots[0][0] if shots else 0.0


def _to_plan(shots: list[Interval]) -> BeatPlan:
    return BeatPlan(start=shots[0][0], end=shots[-1][1], shots=list(shots))


def plan_beats(
    shots: Sequence[Interval], speech: Sequence[Interval], min_sec: float, max_sec: float
) -> list[BeatPlan]:
    _validate_limits(min_sec, max_sec)
    pieces = [piece for shot in shots for piece in split_long_shot(shot, speech, min_sec, max_sec)]
    groups: list[list[Interval]] = []
    current: list[Interval] = []
    for piece in pieces:
        if current and _length([*current, piece]) > max_sec:
            groups.append(current)
            current = []
        current = [*current, piece]
        if _length(current) >= min_sec and not in_speech(piece[1], speech):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [_to_plan(g) for g in _merge_short_tail(groups, min_sec, max_sec)]


def _merge_short_tail(groups: list[list[Interval]], min_sec: float, max_sec: float) -> list[list[Interval]]:
    if len(groups) < 2 or _length(groups[-1]) >= min_sec:
        return groups
    combined = [*groups[-2], *groups[-1]]
    if _length(combined) > max_sec:
        return groups
    return [*groups[:-2], combined]


# ---- dialogue --------------------------------------------------------------

def dialogue_between(segments: Sequence[TranscriptSegment], start: float, end: float) -> str:
    """Text spoken in [start, end), assigning each word (or word-less segment) by its midpoint."""
    parts: list[str] = []
    for seg in segments:
        if seg.words:
            parts.extend(w.word.strip() for w in seg.words if start <= (w.start + w.end) / 2 < end)
        elif start <= (seg.start + seg.end) / 2 < end:
            parts.append(seg.text.strip())
    return " ".join(p for p in parts if p)
