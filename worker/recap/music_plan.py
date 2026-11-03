"""Splitting the recap into background-music cues, and checking their English prompts (pure, no I/O).

The LLM reads the script lines with their place in the recap and answers with cues: runs of
consecutive lines that share one mood, each with a music prompt and an alternative one.
Prompts must not name the film, its characters or any artist, so names are blocked here.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from recap.fit import TimelineEntry

MIN_CUE_SEC = 45  # shorter cues change mood too often to feel like a soundtrack
MAX_CUES = 8
MIN_NAME_CHARS = 3  # shorter capitalized tokens are too often ordinary words
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60
KIND_LABELS = {"narrate": "kể", "original": "thoại gốc"}

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_SENTENCE_START = re.compile(r"(^|[.!?…:]\s+)$")
_STYLE_REFERENCE = re.compile(r"\b(in the style of|style of|sounds? like|inspired by|cover of|soundtrack (of|from)|feat\.?)\b",
                              re.IGNORECASE)  # fmt: skip


@dataclass(frozen=True)
class Cue:
    id: str
    first_line: str
    last_line: str
    out_start: float
    out_end: float
    mood: str
    prompt: str
    alt_prompt: str


def fold(text: str) -> str:
    """Lowercase without Vietnamese accents ("Cóc" -> "coc", "Đăng" -> "dang")."""
    decomposed = unicodedata.normalize("NFD", text.replace("Đ", "D").replace("đ", "d"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _clock(seconds: float) -> str:
    total = round(seconds)
    hours, rest = divmod(total, SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, SECONDS_PER_MINUTE)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def cue_input(timeline: Sequence[TimelineEntry], texts: Mapping[str, str], dialogue: Mapping[str, str]) -> str:
    """One line per script line: `[L0001 00:00-00:20 kể] text` (original lines show their dialogue)."""
    lines = []
    for e in timeline:
        head = f"[{e.id} {_clock(e.out_start)}-{_clock(e.out_end)} {KIND_LABELS.get(e.type, e.type)}]"
        body = texts.get(e.id, "") if e.type == "narrate" else f'"{dialogue.get(e.id, "").strip()}"'
        lines.append(f"{head} {body}".rstrip())
    return "\n".join(lines)


def banned_terms(title: str, texts: Iterable[str]) -> set[str]:
    """Folded words that would identify the film: title words and capitalized words inside sentences."""
    banned = {fold(w) for w in _WORD.findall(title) if len(w) >= MIN_NAME_CHARS}
    for text in texts:
        for match in _WORD.finditer(text):
            word = match.group()
            if word[0].isupper() and len(word) >= MIN_NAME_CHARS and not _SENTENCE_START.search(text[: match.start()]):
                banned.add(fold(word))
    return banned


def prompt_problems(prompt: str, banned: set[str]) -> list[str]:
    """Why a music prompt cannot be used (empty list = fine)."""
    problems: list[str] = []
    if not prompt.strip():
        return ["prompt trống"]
    if not prompt.isascii():
        problems.append("prompt phải viết bằng tiếng Anh, không dấu")
    named = sorted({fold(w) for w in _WORD.findall(prompt)} & banned)
    if named:
        problems.append(f"prompt chứa tên phim/nhân vật: {', '.join(named)}")
    if _STYLE_REFERENCE.search(prompt):
        problems.append("prompt không được nhắc tới nghệ sĩ hay bài hát có sẵn (style of, sounds like, ...)")
    return problems


def _cue_list(raw: Any) -> list[Any] | None:
    cues = raw.get("cues") if isinstance(raw, Mapping) else raw
    return cues if isinstance(cues, list) and cues else None


def validate_cues(
    raw: Any, timeline: Sequence[TimelineEntry], banned: set[str]
) -> tuple[list[Cue], list[str]]:
    """Check that the cues cover the recap line by line, in order, without gaps. Returns (cues, errors)."""
    entries = _cue_list(raw)
    if entries is None:
        return [], ["Không tìm thấy danh sách cues (YAML phải có khóa cues là một danh sách)"]
    if len(entries) > MAX_CUES:
        return [], [f"Tối đa {MAX_CUES} phần nhạc (đang có {len(entries)})"]

    index = {e.id: i for i, e in enumerate(timeline)}
    errors: list[str] = []
    cues: list[Cue] = []
    expected = 0  # timeline index the next cue must start at
    for n, item in enumerate(entries, start=1):
        where = f"Phần nhạc {n}"
        if not isinstance(item, Mapping):
            errors.append(f"{where}: phải là một object")
            continue
        first, last = str(item.get("from", "")), str(item.get("to", ""))
        if first not in index or last not in index or index[last] < index[first]:
            errors.append(f"{where}: from/to phải là id dòng có trong đầu vào, from trước to ({first}-{last})")
            continue
        if index[first] != expected and expected < len(timeline):
            errors.append(f"{where}: phải bắt đầu từ {timeline[expected].id} (đang là {first}), không bỏ sót hay chồng dòng")
        expected = index[last] + 1
        start, end = timeline[index[first]].out_start, timeline[index[last]].out_end
        if len(entries) > 1 and end - start < MIN_CUE_SEC:
            errors.append(f"{where}: dài {end - start:.0f} giây, mỗi phần nhạc phải dài ít nhất {MIN_CUE_SEC} giây")
        prompt, alt = str(item.get("prompt") or "").strip(), str(item.get("alt_prompt") or "").strip()
        for label, text in (("prompt", prompt), ("alt_prompt", alt)):
            errors.extend(f"{where} {label}: {p}" for p in prompt_problems(text, banned))
        cues.append(Cue(f"M{n:02d}", first, last, start, end, str(item.get("mood") or "").strip(), prompt, alt))
    if expected < len(timeline):
        errors.append(f"Các phần nhạc phải phủ tới dòng cuối {timeline[-1].id}")
    return cues, errors
