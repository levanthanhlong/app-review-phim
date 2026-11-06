"""The app's Script Editor and Scene Browser: show and save script.yaml, mark beats, rewrite one line.

Like `recap set`, every change goes through the worker (it owns the schemas) and marks the
stages it invalidates as stale; the next run then redoes only what changed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from recap import config
from recap.beat_marks import MARKS, load_marks, save_marks
from recap.digest import digest_line
from recap.llm import LLMError, LLMProvider, get_provider
from recap.models import DESCRIBED_ADAPTER, SCENES_ADAPTER, Beat, DescribedBeat, Settings
from recap.runner import Stage
from recap.script_model import Item, load_script, parse_tc, validate_items
from recap.settings_update import save_marking_stale
from recap.stages.script import DESCRIBED, SCRIPT, _dump_yaml, format_rate, voice_values
from recap.tts_engines import TTSError
from recap.voices import settings_words_per_sec

SCENES = "work/scenes.json"
NO_MARK = "none"
CONTEXT_LINES = 3  # lines before and after sent with a rewrite
MIN_WORDS, MAX_WORDS = 8, 25  # narration guide: 8–25 words per line
REWRITE_ATTEMPTS = 2
MAX_ERRORS_SHOWN = 3

ProviderFactory = Callable[[Settings], LLMProvider]
SpeechRate = Callable[[Settings], float]

_FENCE_LINE = re.compile(r"^```\w*$")
_QUOTES = "\"'“”‘’«» "


class ScriptEditError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


# ---- reading -----------------------------------------------------------------------------------


def _load(project_dir: Path) -> tuple[Any, list[Item]]:
    try:
        return load_script(project_dir / SCRIPT)
    except ValueError as exc:
        raise ScriptEditError("script_invalid", str(exc)) from exc


def _beats(project_dir: Path) -> dict[str, Beat]:
    path = project_dir / SCENES
    if not path.is_file():
        raise ScriptEditError("input_missing", "Thiếu scenes.json, hãy chạy lại stage scenes")
    return {b.id: b for b in SCENES_ADAPTER.validate_json(path.read_bytes())}


def _described(project_dir: Path) -> dict[str, DescribedBeat]:
    path = project_dir / DESCRIBED
    if not path.is_file():
        return {}
    return {b.id: b for b in DESCRIBED_ADAPTER.validate_json(path.read_bytes())}


def _voice_rate(settings: Settings, speech_rate: SpeechRate) -> float | None:
    try:
        return speech_rate(settings)
    except TTSError:  # malformed tts_rate: the editor still opens
        return None


def _in_seconds(item: Item) -> Item:
    """Timecodes as seconds, the form the app edits."""
    if item["type"] == "narrate":
        clips = [{**c, "start": parse_tc(c["start"]), "end": parse_tc(c["end"])} for c in item["clips"]]
        return {**item, "clips": clips}
    return {**item, "start": parse_tc(item["start"]), "end": parse_tc(item["end"])}


def show_script(project_dir: Path, speech_rate: SpeechRate = settings_words_per_sec) -> dict[str, Any]:
    """script.yaml for the editor, plus the current voice's speed (to estimate line lengths)."""
    script, items = _load(project_dir)
    settings = config.load_project(project_dir).settings
    return {
        "title": script.title,
        "target_duration_sec": script.target_duration_sec,
        "words_per_sec": script.words_per_sec,
        "voice_words_per_sec": _voice_rate(settings, speech_rate),
        "items": [_in_seconds(i) for i in items],
    }


# ---- saving ------------------------------------------------------------------------------------


def _check_ids(raw_items: Sequence[Any]) -> None:
    if not isinstance(raw_items, Sequence) or not raw_items:
        raise ScriptEditError("script_invalid", "Kịch bản phải có ít nhất một dòng (items)")
    ids: list[str] = []
    for n, entry in enumerate(raw_items, start=1):
        line_id = entry.get("id") if isinstance(entry, Mapping) else None
        if not line_id:
            raise ScriptEditError("script_invalid", f"Dòng {n}: thiếu id")
        ids.append(str(line_id))
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ScriptEditError("script_invalid", f"Kịch bản trùng id {', '.join(duplicates)}")


def save_script(project_dir: Path, raw_items: Sequence[Any], stages: Mapping[str, Stage]) -> list[str]:
    """Validate the edited lines against the beats, write script.yaml and return the stages marked stale."""
    _check_ids(raw_items)
    script, _ = _load(project_dir)
    items, errors = validate_items(list(raw_items), _beats(project_dir), target_sec=0, check_duration=False)
    if errors:
        raise ScriptEditError("script_invalid", "; ".join(errors[:MAX_ERRORS_SHOWN]))
    header = {"title": script.title, "target_duration_sec": script.target_duration_sec}
    if script.words_per_sec is not None:
        header["words_per_sec"] = script.words_per_sec
    config.write_text_atomic(project_dir / SCRIPT, _dump_yaml({**header, "items": items}))
    return save_marking_stale(project_dir, config.load_project(project_dir), stages)


# ---- beat marks --------------------------------------------------------------------------------


def mark_beat(project_dir: Path, beat_id: str, mark: str, stages: Mapping[str, Stage]) -> list[str]:
    """Set (star / skip / keep) or clear (none) one beat's mark. Returns the stages marked stale."""
    if mark not in (*MARKS, NO_MARK):
        raise ScriptEditError("mark_invalid", f"Đánh dấu {mark!r} không hợp lệ (chọn {', '.join((*MARKS, NO_MARK))})")
    if beat_id not in _beats(project_dir):
        raise ScriptEditError("mark_invalid", f"Không có beat {beat_id}")
    marks = {b: m for b, m in load_marks(project_dir).items() if b != beat_id}
    if mark != NO_MARK:
        marks[beat_id] = mark
    save_marks(project_dir, marks)
    return save_marking_stale(project_dir, config.load_project(project_dir), stages)


# ---- rewriting one line ------------------------------------------------------------------------


def _template() -> str:
    return resources.files("recap.prompts").joinpath("rewrite_line.md").read_text(encoding="utf-8")


def _context_line(item: Item, described: Mapping[str, DescribedBeat], beats: Mapping[str, Beat]) -> str:
    if item["type"] == "narrate":
        return f"- {item['text']}"
    beat = described.get(item["beat"]) or beats.get(item["beat"])
    dialogue = beat.dialogue.strip() if beat else ""
    return f"- (đoạn giữ thoại gốc{': ' + dialogue if dialogue else ''})"


def _scene_lines(item: Item, described: Mapping[str, DescribedBeat], beats: Mapping[str, Beat]) -> list[str]:
    lines = []
    for beat_id in dict.fromkeys(c["beat"] for c in item["clips"]):  # each beat once, in order
        if beat_id in described:
            lines.append(digest_line(described[beat_id]))
        elif beat_id in beats:
            lines.append(f"[{beat_id}] Thoại: {beats[beat_id].dialogue or '(không có)'}")
    return lines


def _rewrite_input(items: list[Item], index: int, project_dir: Path) -> str:
    beats, described = _beats(project_dir), _described(project_dir)
    before = items[max(0, index - CONTEXT_LINES) : index]
    after = items[index + 1 : index + 1 + CONTEXT_LINES]
    sections = [
        ("Các câu trước", [_context_line(i, described, beats) for i in before] or ["(đây là câu đầu tiên)"]),
        ("Cảnh đang chiếu", _scene_lines(items[index], described, beats)),
        ("Các câu sau", [_context_line(i, described, beats) for i in after] or ["(đây là câu cuối cùng)"]),
    ]
    return "\n\n".join(f"{title}:\n" + "\n".join(lines) for title, lines in sections)


def _clean_answer(answer: str) -> str:
    """The first non-empty line of the answer, without code fences, list dash or quotes."""
    for line in answer.splitlines():
        text = line.strip()
        if not text or _FENCE_LINE.match(text):
            continue
        return text.removeprefix("- ").strip(_QUOTES)
    return ""


def _find_narration(items: list[Item], line_id: str) -> int:
    index = next((n for n, i in enumerate(items) if i["id"] == line_id), None)
    if index is None:
        raise ScriptEditError("line_invalid", f"Không có dòng {line_id}")
    if items[index]["type"] != "narrate":
        raise ScriptEditError("line_invalid", f"Dòng {line_id} giữ thoại gốc, không có lời kể để viết lại")
    return index


def rewrite_line(
    project_dir: Path,
    line_id: str,
    provider_factory: ProviderFactory = get_provider,
    speech_rate: SpeechRate = settings_words_per_sec,
    hint: str = "",
) -> str:
    """Ask the LLM for a better version of one narration line (not saved: the app shows it first)."""
    _, items = _load(project_dir)
    index = _find_narration(items, line_id)
    project = config.load_project(project_dir)
    settings = project.settings
    item = items[index]
    rate = _voice_rate(settings, speech_rate) or 3.0
    clip_sec = sum(parse_tc(c["end"]) - parse_tc(c["start"]) for c in item["clips"])
    words = min(MAX_WORDS, max(MIN_WORDS, round(clip_sec * rate)))
    values = {
        **voice_values(settings),
        "title": project.title,
        "line_id": line_id,
        "text": item["text"],
        "hint": f"Người làm video muốn: {hint.strip()}" if hint.strip() else "",
        "words": str(words),
        "words_per_sec": format_rate(rate),
        "clip_sec": f"{clip_sec:.0f}",
    }
    prompt = _template()
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    material = _rewrite_input(items, index, project_dir)
    try:
        provider = provider_factory(settings)
        for _ in range(REWRITE_ATTEMPTS):
            text = _clean_answer(provider.generate(prompt, material))
            if text:
                return text
    except LLMError as exc:
        raise ScriptEditError(exc.code, exc.message, exc.retryable) from exc
    raise ScriptEditError("llm_failed", "Model AI không trả về câu nào, hãy thử lại", retryable=True)
