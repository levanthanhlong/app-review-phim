"""Compact text digest of described beats, split into ~30-minute parts for the LLM."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from recap.models import DescribedBeat
from recap.script_model import format_tc

SECONDS_PER_MINUTE = 60
NO_DIALOGUE = "(không có)"
IMPORTANT_FLAG = "⭐ QUAN TRỌNG"
CAST_LABEL = "Nhân vật"


def digest_line(beat: DescribedBeat, important: bool = False, names: Sequence[str] = ()) -> str:
    dialogue = f'"{beat.dialogue.strip()}"' if beat.dialogue.strip() else NO_DIALOGUE
    flag = f" | {IMPORTANT_FLAG}" if important else ""
    people = f" | {CAST_LABEL}: {', '.join(names)}" if names else ""
    return f"[{beat.id} {format_tc(beat.start)}-{format_tc(beat.end)}] Hình: {beat.desc} | Thoại: {dialogue}{flag}{people}"


def digest_text(
    beats: Sequence[DescribedBeat], starred: frozenset[str] = frozenset(), cast: Mapping[str, Sequence[str]] | None = None
) -> str:  # fmt: skip
    """One line per beat; `cast` adds who is on screen (named in the "Nhân vật" tab)."""
    cast = cast or {}
    return "\n".join(digest_line(b, b.id in starred, cast.get(b.id, ())) for b in beats)


def split_parts(beats: Sequence[DescribedBeat], part_size_min: float) -> list[list[DescribedBeat]]:
    """Consecutive groups of beats, a new group starting every `part_size_min` minutes."""
    size = part_size_min * SECONDS_PER_MINUTE
    parts: list[list[DescribedBeat]] = []
    part_start = None
    for beat in beats:
        if part_start is None or beat.start >= part_start + size:
            parts.append([])
            part_start = beat.start
        parts[-1].append(beat)
    return parts
