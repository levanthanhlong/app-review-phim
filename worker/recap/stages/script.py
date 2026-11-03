"""Stage `script`: digest -> LLM per ~30-minute part (sequential, with retries) -> script.yaml.

Each finished part is saved in work/script_parts/part_XX.yaml together with a key of
everything that produced it, so a run that stopped (quota, crash, cancel) resumes at
the first unfinished part.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from recap import config
from recap.digest import digest_text, split_parts
from recap.llm import LLMError, LLMProvider, get_provider
from recap.models import DESCRIBED_ADAPTER, DescribedBeat, Project, Settings
from recap.runner import StageContext, StageFailure
from recap.script_model import Item, parse_response, validate_items
from recap.tts_engines import TTSError
from recap.voices import settings_words_per_sec

DESCRIBED = "work/scenes_described.json"
SCRIPT = "script.yaml"
DIGEST_DIR = "work/digest"
PARTS_DIR = "work/script_parts"
MAX_RETRIES = 2
FIRST_PART_SUMMARY = "(đây là phần đầu tiên)"
SPEED_TOLERANCE = 0.15  # a voice this close to the speed the script was written for still fits it
STYLES = {
    "kich_tinh": "kịch tính, lôi cuốn",
    "nghiem_tuc": "nghiêm túc, mạch lạc",
    "hai_mia_mai": "hài hước, mỉa mai",
}

ProviderFactory = Callable[[Settings], LLMProvider]
SpeechRate = Callable[[Settings], float]


def format_rate(rate: float) -> str:
    """Words/second for people and prompts: one decimal, no trailing `.0` (4.5, 3)."""
    return f"{rate:.1f}".removesuffix(".0")


def _template() -> str:
    return resources.files("recap.prompts").joinpath("write_script.md").read_text(encoding="utf-8")


def _guide() -> str:
    """Narration craft guide, also used by the Claude Code skill `recap-scriptwriting`."""
    return resources.files("recap.prompts").joinpath("narration_guide.md").read_text(encoding="utf-8").strip()


def _fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


_TIMECODE = re.compile(r"\d{2}:\d{2}:\d{2}(\.\d+)?")


class _ScriptDumper(yaml.SafeDumper):
    """Always double-quote timecodes: unquoted `01:02:05` is a base-60 int (3725) in YAML 1.1."""

    def represent_str(self, data: str) -> yaml.ScalarNode:
        if _TIMECODE.fullmatch(data):
            return self.represent_scalar("tag:yaml.org,2002:str", data, style='"')
        return super().represent_str(data)


_ScriptDumper.add_representer(str, _ScriptDumper.represent_str)


def _dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_ScriptDumper, allow_unicode=True, sort_keys=False, width=1000)


def _part_targets(parts: list[list[DescribedBeat]], total_target: float) -> list[float]:
    """Split the total target between parts in proportion to their running time."""
    lengths = [p[-1].end - p[0].start for p in parts]
    whole = sum(lengths) or 1.0
    return [total_target * length / whole for length in lengths]


class ScriptStage:
    name = "script"

    def __init__(self, provider_factory: ProviderFactory = get_provider, speech_rate: SpeechRate = settings_words_per_sec) -> None:
        self._provider_factory = provider_factory
        self._speech_rate = speech_rate

    def _fingerprint_speed(self, project_dir: Path, settings: Settings) -> float:
        """The voice speed, or the script's own speed when the voice is within SPEED_TOLERANCE of it."""
        speed = self._words_per_sec(settings)
        try:
            written_for = yaml.safe_load((project_dir / SCRIPT).read_text(encoding="utf-8")).get("words_per_sec")
        except (FileNotFoundError, yaml.YAMLError, AttributeError):
            return speed
        if isinstance(written_for, (int, float)) and written_for > 0:
            if abs(speed - written_for) / written_for <= SPEED_TOLERANCE:
                return float(written_for)
        return speed

    def _words_per_sec(self, settings: Settings) -> float:
        try:
            return self._speech_rate(settings)
        except TTSError as exc:  # malformed tts_rate
            raise StageFailure("settings_invalid", str(exc)) from exc

    def outputs(self, project: Project) -> list[str]:
        return [SCRIPT]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        s = project.settings
        duration = project.media.duration if project.media else 0.0
        prompt_hash = hashlib.sha256((_template() + _guide()).encode()).hexdigest()[:12]
        speed = f"{self._fingerprint_speed(project_dir, s):.2f}"  # a much faster/slower voice needs other text
        fields = [s.target_ratio, s.narration_style, s.llm_provider, s.claude_model, s.part_size_min, duration, speed]
        return "|".join([config.file_signature(project_dir / DESCRIBED), prompt_hash, *map(str, fields)])

    def run(self, ctx: StageContext) -> Project:
        beats = self._load_beats(ctx)
        settings = ctx.project.settings
        total_duration = ctx.project.media.duration if ctx.project.media else beats[-1].end
        total_target = round(total_duration * settings.target_ratio)
        speed = self._words_per_sec(settings)
        parts = split_parts(beats, settings.part_size_min)
        targets = _part_targets(parts, total_target)
        ctx.path(DIGEST_DIR).mkdir(parents=True, exist_ok=True)
        ctx.path(PARTS_DIR).mkdir(parents=True, exist_ok=True)
        if ctx.force:  # write every part anew; a forced run that stops resumes from the new parts
            for saved in ctx.path(PARTS_DIR).glob("part_*"):
                saved.unlink()
        try:
            provider = self._provider_factory(settings)
        except LLMError as exc:
            raise StageFailure(exc.code, exc.message, exc.retryable) from exc

        all_items: list[Item] = []
        summary = FIRST_PART_SUMMARY
        for index, (part, target) in enumerate(zip(parts, targets), start=1):
            ctx.emitter.progress(self.name, index - 1, len(parts), f"Phần {index}/{len(parts)}")
            items, summary = self._part(ctx, provider, part, index, len(parts), target, summary, speed)
            all_items.extend(items)

        script = {
            "title": ctx.project.title,
            "target_duration_sec": total_target,
            "words_per_sec": round(speed, 2),
            # Renumber globally: every part's answer starts its own ids at L0001.
            "items": [
                {"id": f"L{n:04d}", **{k: v for k, v in item.items() if k != "id"}}
                for n, item in enumerate(all_items, start=1)
            ],
        }
        config.write_text_atomic(ctx.path(SCRIPT), _dump_yaml(script))
        ctx.emitter.progress(self.name, len(parts), len(parts), "Xong")
        ctx.emitter.log("info", f"{len(parts)} phần, {len(all_items)} dòng kịch bản, mục tiêu {total_target}s")
        ctx.emitter.artifact(self.name, SCRIPT)
        return ctx.project

    def _load_beats(self, ctx: StageContext) -> list[DescribedBeat]:
        path = ctx.path(DESCRIBED)
        if not path.is_file():
            raise StageFailure("input_missing", "Thiếu scenes_described.json, hãy chạy lại stage describe")
        beats = DESCRIBED_ADAPTER.validate_json(path.read_bytes())
        if not beats:
            raise StageFailure("input_missing", "scenes_described.json không có beat nào")
        return beats

    def _part(
        self,
        ctx: StageContext,
        provider: LLMProvider,
        beats: list[DescribedBeat],
        index: int,
        total: int,
        target: float,
        previous_summary: str,
        speed: float,
    ) -> tuple[list[Item], str]:
        digest = digest_text(beats)
        config.write_text_atomic(ctx.path(f"{DIGEST_DIR}/part_{index:02d}.txt"), digest)
        settings = ctx.project.settings
        prompt = _fill(_template(), {
            "style": STYLES.get(settings.narration_style, settings.narration_style),
            "title": ctx.project.title,
            "part": str(index),
            "total_parts": str(total),
            "previous_summary": previous_summary,
            "target_sec": f"{target:.0f}",
            "words_per_sec": format_rate(speed),
            "guide": _guide(),
        })  # fmt: skip
        key = hashlib.sha256(f"{settings.claude_model}\n{prompt}\n{digest}".encode()).hexdigest()[:16]
        part_file = ctx.path(f"{PARTS_DIR}/part_{index:02d}.yaml")
        cached = self._load_part(part_file, key)
        if cached is not None:
            ctx.emitter.log("info", f"Phần {index}/{total}: dùng kết quả đã có")
            return cached
        items, summary = self._ask(ctx, provider, prompt, digest, {b.id: b for b in beats}, target, index, speed)
        config.write_text_atomic(part_file, _dump_yaml({"key": key, "summary": summary, "items": items}))
        return items, summary

    @staticmethod
    def _load_part(path: Path, key: str) -> tuple[list[Item], str] | None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, yaml.YAMLError):
            return None
        if not isinstance(data, dict) or data.get("key") != key:
            return None
        return data["items"], data.get("summary", "")

    def _ask(
        self,
        ctx: StageContext,
        provider: LLMProvider,
        prompt: str,
        digest: str,
        beats: dict[str, DescribedBeat],
        target: float,
        index: int,
        speed: float,
    ) -> tuple[list[Item], str]:
        """Call the LLM; on invalid output retry up to MAX_RETRIES times with the errors attached."""
        current_prompt, answer, errors = prompt, "", []
        for attempt in range(MAX_RETRIES + 1):
            ctx.emitter.log("info", f"Phần {index}: gọi Claude (lần {attempt + 1})")
            try:
                answer = provider.generate(current_prompt, digest)
            except LLMError as exc:
                raise StageFailure(exc.code, exc.message, exc.retryable) from exc
            try:
                data, summary = parse_response(answer)
                items, errors = validate_items(data, beats, target, words_per_sec=speed)
            except ValueError as exc:
                items, summary, errors = [], "", [str(exc)]
            if not errors:
                return items, summary
            ctx.emitter.log("warning", f"Phần {index} lần {attempt + 1} chưa hợp lệ: {'; '.join(errors[:3])}")
            if attempt == MAX_RETRIES and all(e.startswith("Tổng thời lượng") for e in errors):
                ctx.emitter.log("warning", f"Phần {index}: chấp nhận dù lệch thời lượng mục tiêu")
                return items, summary
            current_prompt = prompt + "\n\nLần trả lời trước có các lỗi sau:\n- " + "\n- ".join(errors) + (
                "\nHãy viết lại TOÀN BỘ kết quả và sửa hết các lỗi trên."
            )
        config.write_text_atomic(ctx.path(f"{PARTS_DIR}/part_{index:02d}.raw.txt"), answer)
        raise StageFailure(
            "script_invalid",
            f"Phần {index}: Claude trả kết quả sai sau {MAX_RETRIES + 1} lần ({errors[0]}). "
            f"Bản thô lưu ở {PARTS_DIR}/part_{index:02d}.raw.txt",
            retryable=True,
        )
