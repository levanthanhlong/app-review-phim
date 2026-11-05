"""People's names said in the dialogue, for the user to pick from in the "Nhân vật" tab.

One LLM call lists the names (with their spelling in the dialogue, which may be Thai or Korean
script); the worker then counts them and quotes real lines, so a name the LLM invented, said
nowhere in the dialogue, is dropped. A failure only costs the list: the user can still type names.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources

from recap.llm import LLMError, LLMProvider
from recap.models import TranscriptSegment
from recap.script_model import format_tc

ATTEMPTS = 2
EXAMPLES = 3  # lines quoted per name

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
# Thai, Lao, Myanmar, Khmer, Chinese and Japanese: no spaces between words, so no word boundaries
_NO_SPACES = re.compile("[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3040-\u30ff\u3400-\u9fff]")


@dataclass(frozen=True)
class NameCandidate:
    name: str
    count: int  # lines of dialogue that say it
    examples: tuple[str, ...]


def _template() -> str:
    return resources.files("recap.prompts").joinpath("find_names.md").read_text(encoding="utf-8")


def _parse(answer: str) -> list[tuple[str, str]] | None:
    block = _JSON_BLOCK.search(answer)
    try:
        data = json.loads(block.group(1) if block else answer)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    pairs = []
    for entry in data:
        if isinstance(entry, dict) and str(entry.get("name", "")).strip():
            name = str(entry["name"]).strip()
            pairs.append((name, str(entry.get("original") or name).strip()))
    return pairs


def _matcher(original: str) -> re.Pattern[str]:
    """Whole words (Hiro, not Hiroshi), except in scripts written without spaces between words."""
    escaped = re.escape(original)
    if _NO_SPACES.search(original):
        return re.compile(escaped, re.IGNORECASE)
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def _candidates(pairs: list[tuple[str, str]], dialogue: Sequence[TranscriptSegment]) -> list[NameCandidate]:
    found: dict[str, tuple[int, list[str]]] = {}  # name -> (first line index, lines)
    for name, original in pairs:
        pattern = _matcher(original)
        lines = [(n, seg.text.strip()) for n, seg in enumerate(dialogue) if pattern.search(seg.text)]
        if not lines or name in found:
            continue
        found[name] = (lines[0][0], [text for _, text in lines])
    ranked = sorted(found.items(), key=lambda item: (-len(item[1][1]), item[1][0]))
    return [NameCandidate(name, len(lines), tuple(lines[:EXAMPLES])) for name, (_, lines) in ranked]


def find_names(dialogue: Sequence[TranscriptSegment], provider: LLMProvider) -> tuple[list[NameCandidate], str | None]:
    """(names said in the dialogue, most often first; a warning when the LLM could not help)."""
    if not dialogue:
        return [], None
    material = "\n".join(f"[{format_tc(seg.start).split('.')[0]}] {seg.text.strip()}" for seg in dialogue)
    try:
        for _ in range(ATTEMPTS):
            pairs = _parse(provider.generate(_template(), material))
            if pairs is not None:
                return _candidates(pairs, dialogue), None
    except LLMError as exc:
        return [], f"Không lấy được tên từ lời thoại ({exc.message}); vẫn có thể tự gõ tên"
    return [], "Claude không trả về danh sách tên hợp lệ; vẫn có thể tự gõ tên"
