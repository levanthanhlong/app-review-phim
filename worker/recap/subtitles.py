"""Subtitle cues for the narration (pure), and SRT output.

A narration line is shown while its TTS plays. Long lines are split into several cues of at
most two lines each (preferring to break after punctuation), and the line's audio time is
shared between them in proportion to their length in characters.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from recap.fit import TimelineEntry
from recap.render import NARRATION_DELAY_SEC

MAX_LINE_CHARS = 42  # common subtitle guideline for one line
MAX_CUE_CHARS = 2 * MAX_LINE_CHARS
MIN_BREAK_CHARS = 30  # break after punctuation only once a cue has this much text
BREAK_AFTER = (",", ".", ";", ":", "!", "?", "…")
MS_PER_SEC = 1000
MAX_GAP_FILL_SEC = 0.5  # a shorter blank between two cues would only flicker


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str  # may contain one "\n"


def split_cue_text(text: str) -> list[str]:
    """Split a narration line into cue-sized chunks (≤ MAX_CUE_CHARS unless one word is longer)."""
    chunks: list[str] = []
    current: list[str] = []
    for word in text.split():
        candidate = " ".join([*current, word])
        if current and len(candidate) > MAX_CUE_CHARS:
            chunks.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
        joined = " ".join(current)
        if word.endswith(BREAK_AFTER) and len(joined) >= MIN_BREAK_CHARS:
            chunks.append(joined)
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks


def wrap_lines(text: str) -> str:
    """Break a chunk into two lines at the word boundary nearest the middle, if it is too long."""
    if len(text) <= MAX_LINE_CHARS:
        return text
    words = text.split()
    best = min(
        range(1, len(words)),
        key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))),
        default=None,
    )
    if best is None:
        return text
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def _line_cues(start: float, duration: float, text: str) -> list[Cue]:
    chunks = split_cue_text(unicodedata.normalize("NFC", text))
    total = sum(len(c) for c in chunks) or 1
    cues: list[Cue] = []
    at = start
    for n, chunk in enumerate(chunks):
        end = start + duration if n == len(chunks) - 1 else at + duration * len(chunk) / total
        cues.append(Cue(at, end, wrap_lines(chunk)))
        at = end
    return cues


def build_cues(timeline: Sequence[TimelineEntry], texts: Mapping[str, str]) -> list[Cue]:
    """Cues for every narration line, timed to its TTS inside the recap."""
    cues: list[Cue] = []
    for entry in timeline:
        text = texts.get(entry.id, "").strip()
        if entry.type != "narrate" or not text or not entry.tts_dur:
            continue
        cues.extend(_line_cues(entry.out_start + NARRATION_DELAY_SEC, entry.tts_dur, text))
    return _close_short_gaps(cues)


def _close_short_gaps(cues: list[Cue]) -> list[Cue]:
    closed = [
        Cue(cue.start, nxt.start, cue.text) if 0 < nxt.start - cue.end <= MAX_GAP_FILL_SEC else cue
        for cue, nxt in zip(cues, cues[1:])
    ]
    return [*closed, *cues[-1:]]


def format_srt_time(sec: float) -> str:
    total_ms = round(sec * MS_PER_SEC)
    hours, rest = divmod(total_ms, 3600 * MS_PER_SEC)
    minutes, rest = divmod(rest, 60 * MS_PER_SEC)
    seconds, ms = divmod(rest, MS_PER_SEC)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


def to_srt(cues: Sequence[Cue]) -> str:
    blocks = [
        f"{n}\n{format_srt_time(c.start)} --> {format_srt_time(c.end)}\n{c.text}\n" for n, c in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)
