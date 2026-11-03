"""script.yaml: timecodes, parsing of the LLM answer, and validation against the beats.

Validation returns human-readable Vietnamese errors; they are sent back to the LLM on retry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, Field, ValidationError

from recap.models import Beat

WORDS_PER_SEC = 3.0  # default narration speed; the script stage passes the chosen voice's measured rate
PAD_SEC = 0.25  # the assembler holds every narrate line this much longer than its TTS
DURATION_TOLERANCE = 0.15  # ±15% of the part's target
BEAT_EDGE_TOLERANCE_SEC = 0.25  # small overshoots are clamped instead of rejected
SECONDS_PER_MINUTE = 60

_FENCE = re.compile(r"```(?:yaml|yml)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_SUMMARY = re.compile(r"^\s*SUMMARY:", re.MULTILINE)

Item = dict[str, Any]


# ---- timecodes -------------------------------------------------------------------

def parse_tc(value: object) -> float:
    """'HH:MM:SS.s', 'MM:SS.s', '12.5' or a number -> seconds."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Mốc thời gian không hợp lệ: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    if not parts[0] or len(parts) > 3:
        raise ValueError(f"Mốc thời gian không hợp lệ: {value!r}")
    try:
        numbers = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"Mốc thời gian không hợp lệ: {value!r}") from exc
    if any(n < 0 for n in numbers) or any(n >= SECONDS_PER_MINUTE for n in numbers[1:]):
        raise ValueError(f"Mốc thời gian không hợp lệ: {value!r}")
    seconds = 0.0
    for n in numbers:
        seconds = seconds * SECONDS_PER_MINUTE + n
    return seconds


def format_tc(seconds: float) -> str:
    """Seconds -> 'HH:MM:SS[.fff]' with trailing zeros of the fraction dropped."""
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{text}.{ms:03d}".rstrip("0") if ms else text


# ---- LLM answer ---------------------------------------------------------------------

def parse_response(text: str) -> tuple[Any, str]:
    """Split an answer into (parsed YAML, summary). Raises ValueError on unparsable YAML."""
    match = _SUMMARY.search(text)
    body, summary_text = (text[: match.start()], text[match.end():]) if match else (text, "")
    fence = _FENCE.search(body)
    yaml_text = fence.group(1) if fence else body
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML không hợp lệ: {exc}") from exc
    summary = " ".join(line.strip() for line in summary_text.splitlines() if line.strip())
    return data, summary


# ---- validation -------------------------------------------------------------------

def _check_range(
    where: str, beat_id: object, start: object, end: object, beats: Mapping[str, Beat], errors: list[str]
) -> tuple[str, str] | None:
    """Validate a (beat, start, end) reference; returns normalized timecodes or None if invalid."""
    beat = beats.get(str(beat_id))
    if beat is None:
        errors.append(f"{where}: beat {beat_id} không có trong đầu vào")
        return None
    try:
        s, e = parse_tc(start), parse_tc(end)
    except ValueError as exc:
        errors.append(f"{where}: {exc}")
        return None
    tol = BEAT_EDGE_TOLERANCE_SEC
    if s < beat.start - tol or e > beat.end + tol:
        errors.append(
            f"{where}: mốc {format_tc(s)}-{format_tc(e)} nằm ngoài beat {beat.id} "
            f"({format_tc(beat.start)}-{format_tc(beat.end)})"
        )
        return None
    s, e = max(s, beat.start), min(e, beat.end)
    if e <= s:
        errors.append(f"{where}: mốc kết thúc phải sau mốc bắt đầu")
        return None
    return format_tc(s), format_tc(e)


def _validate_narrate(where: str, raw: Mapping[str, Any], beats: Mapping[str, Beat], errors: list[str]) -> Item | None:
    text = str(raw.get("text") or "").strip()
    clips_raw = raw.get("clips")
    if not text:
        errors.append(f"{where}: thiếu text")
    if not isinstance(clips_raw, list) or not clips_raw:
        errors.append(f"{where}: narrate phải có ít nhất 1 clip")
        return None
    clips = []
    for j, clip in enumerate(clips_raw, start=1):
        if not isinstance(clip, Mapping):
            errors.append(f"{where} clip {j}: phải có beat, start, end")
            continue
        times = _check_range(f"{where} clip {j}", clip.get("beat"), clip.get("start"), clip.get("end"), beats, errors)
        if times:
            clips.append({"beat": str(clip["beat"]), "start": times[0], "end": times[1]})
    if not text or len(clips) != len(clips_raw):
        return None
    return {"type": "narrate", "text": text, "clips": clips}


def _validate_original(where: str, raw: Mapping[str, Any], beats: Mapping[str, Beat], errors: list[str]) -> Item | None:
    times = _check_range(where, raw.get("beat"), raw.get("start"), raw.get("end"), beats, errors)
    if times is None:
        return None
    item: Item = {"type": "original", "beat": str(raw["beat"]), "start": times[0], "end": times[1]}
    if raw.get("note"):
        item["note"] = str(raw["note"])
    return item


def estimate_duration(items: list[Item], words_per_sec: float = WORDS_PER_SEC) -> float:
    total = 0.0
    for item in items:
        if item["type"] == "narrate":
            total += len(item["text"].split()) / words_per_sec + PAD_SEC
        else:
            total += parse_tc(item["end"]) - parse_tc(item["start"])
    return total


def duration_error(items: list[Item], target_sec: float, words_per_sec: float = WORDS_PER_SEC) -> str | None:
    estimate = estimate_duration(items, words_per_sec)
    if target_sec > 0 and abs(estimate - target_sec) / target_sec > DURATION_TOLERANCE:
        return (
            f"Tổng thời lượng ước tính {estimate:.0f} giây, mục tiêu {target_sec:.0f} giây "
            f"(cho phép ±{DURATION_TOLERANCE:.0%}). Hãy {'bớt' if estimate > target_sec else 'thêm'} nội dung."
        )
    return None


def validate_items(
    raw: Any,
    beats: Mapping[str, Beat],
    target_sec: float,
    check_duration: bool = True,
    words_per_sec: float = WORDS_PER_SEC,
) -> tuple[list[Item], list[str]]:
    """Validate and normalize items (an existing `id` is kept). Returns (items, errors)."""
    raw_items = raw.get("items") if isinstance(raw, Mapping) else raw
    if not isinstance(raw_items, list) or not raw_items:
        return [], ["Không tìm thấy danh sách items (YAML phải có khóa items là một danh sách)"]
    errors: list[str] = []
    items: list[Item] = []
    for n, entry in enumerate(raw_items, start=1):
        where = f"Dòng {n}"
        if not isinstance(entry, Mapping):
            errors.append(f"{where}: phải là một object")
            continue
        kind = entry.get("type")
        if kind == "narrate":
            item = _validate_narrate(where, entry, beats, errors)
        elif kind == "original":
            item = _validate_original(where, entry, beats, errors)
        else:
            errors.append(f"{where}: type phải là narrate hoặc original (đang là {kind!r})")
            item = None
        if item:
            items.append({"id": str(entry["id"]), **item} if entry.get("id") else item)
    if not errors and check_duration:
        problem = duration_error(items, target_sec, words_per_sec)
        if problem:
            errors.append(problem)
    return items, errors


# ---- script.yaml file ----------------------------------------------------------------

TimeValue = Union[str, float]


class _ScriptClip(BaseModel):
    beat: str
    start: TimeValue
    end: TimeValue


class _Narrate(BaseModel):
    id: str
    type: Literal["narrate"]
    text: str = Field(min_length=1)
    clips: list[_ScriptClip] = Field(min_length=1)


class _Original(BaseModel):
    id: str
    type: Literal["original"]
    beat: str
    start: TimeValue
    end: TimeValue
    note: str | None = None


class ScriptFile(BaseModel):
    title: str = ""
    target_duration_sec: float = 0
    words_per_sec: float | None = None  # narration speed the script was written for
    items: list[Annotated[Union[_Narrate, _Original], Field(discriminator="type")]] = Field(min_length=1)


def load_script(path: Path) -> tuple[ScriptFile, list[Item]]:
    """Read script.yaml (possibly hand-edited). Raises ValueError with a readable message."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        script = ScriptFile.model_validate(data)
    except FileNotFoundError as exc:
        raise ValueError(f"Không tìm thấy {path.name}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"{path.name} không hợp lệ: {exc}") from exc
    ids = [item.id for item in script.items]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"{path.name}: trùng id {', '.join(duplicates)}")
    return script, [item.model_dump(exclude_none=True) for item in script.items]
