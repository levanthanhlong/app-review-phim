"""Revise the whole script from the user's notes (Script Editor: "Nhận xét & viết lại").

The script is split into the same ~30-minute parts as the script stage; for each part Claude gets
the stage's instructions plus the notes, and on stdin the part's beats and its current lines
(hand edits included). Answers are validated and retried like the stage's. Nothing is written
unless every part succeeds; the replaced script goes to work/script_backups/.

The script stage stays DONE with its fingerprint (its inputs did not change), so a later run does
not undo the revision; only its finish time changes, which tells the app to reload the editor.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from importlib import resources
from pathlib import Path

from recap import config
from recap.characters import project_cast
from recap.digest import digest_text, split_parts
from recap.events import Emitter
from recap.llm import LLMError, LLMProvider, get_provider
from recap.models import DescribedBeat, Project, Settings, StageStatus, now_iso
from recap.runner import Stage, StageContext, StageFailure
from recap.script_edit import ScriptEditError, _described, _load
from recap.script_model import Item, parse_tc
from recap.settings_update import save_marking_stale
from recap.stages.script import SCRIPT, ScriptStage, _dump_yaml, part_prompt, part_targets, script_text
from recap.tts_engines import TTSError
from recap.voices import settings_words_per_sec

NAME = "script"
LOG = "logs/script.log"
CURRENT_HEADER = "KỊCH BẢN HIỆN TẠI của phần này:"

ProviderFactory = Callable[[Settings], LLMProvider]
SpeechRate = Callable[[Settings], float]


def _template() -> str:
    return resources.files("recap.prompts").joinpath("revise_script.md").read_text(encoding="utf-8")


def _first_beat(item: Item) -> str:
    return item["clips"][0]["beat"] if item["type"] == "narrate" else item["beat"]


def _group(items: list[Item], parts: list[list[DescribedBeat]], described: Mapping[str, DescribedBeat]) -> list[list[Item]]:
    """The lines of each part: a line belongs to the part its first beat falls in (by time)."""
    starts = [part[0].start for part in parts]
    groups: list[list[Item]] = [[] for _ in parts]
    for item in items:
        beat = described.get(_first_beat(item))
        at = beat.start if beat else parse_tc(item["clips"][0]["start"] if item["type"] == "narrate" else item["start"])
        index = max((i for i, start in enumerate(starts) if start <= at), default=0)
        groups[index].append(item)
    return groups


def _speed(script_speed: float | None, settings: Settings, speech_rate: SpeechRate) -> float:
    """The speed the script was written for (kept, so its fingerprint holds), else the voice's."""
    if script_speed:
        return script_speed
    try:
        return speech_rate(settings)
    except TTSError as exc:
        raise ScriptEditError("settings_invalid", str(exc)) from exc


def _revise_parts(
    ctx: StageContext, stage: ScriptStage, provider: LLMProvider, items: list[Item], notes: str, target_sec: float, speed: float
) -> list[Item]:  # fmt: skip
    described = _described(ctx.project_dir)
    beats, starred = stage.load_beats(ctx)
    cast = project_cast(ctx.project_dir)
    parts = split_parts(beats, ctx.project.settings.part_size_min)
    targets = part_targets(parts, target_sec)
    groups = _group(items, parts, described)
    revised: list[Item] = []
    summary = "(đây là phần đầu tiên)"
    for index, (part, target, lines) in enumerate(zip(parts, targets, groups), start=1):
        ctx.emitter.progress(NAME, index - 1, len(parts), f"Viết lại phần {index}/{len(parts)}")
        if not lines:
            continue
        prompt = part_prompt(ctx.project.settings, ctx.project.title, index, len(parts), summary, target, speed,
                             has_starred=any(b.id in starred for b in part), cast=cast.names)  # fmt: skip
        prompt += _template().replace("{feedback}", notes)
        material = f"{digest_text(part, starred, cast.by_beat)}\n\n{CURRENT_HEADER}\n```yaml\n{_dump_yaml({'items': lines})}```\n"
        # beats of hand-picked clips outside the part stay usable
        usable = {**{b.id: b for b in part}, **{b: described[b] for b in map(_first_beat, lines) if b in described}}
        new_items, summary = stage.ask(ctx, provider, prompt, material, usable, target, index, speed)
        revised.extend(new_items)
    ctx.emitter.progress(NAME, len(parts), len(parts), "Xong")
    return revised


def _mark_rewritten(project_dir: Path, project: Project, stages: Mapping[str, Stage]) -> list[str]:
    """The script stage keeps its status and fingerprint, with a new finish time; later stages go stale."""
    state = project.stages[NAME]
    if state.status == StageStatus.DONE:
        project = project.with_stage(NAME, state.model_copy(update={"finished_at": now_iso()}))
    return save_marking_stale(project_dir, project, stages)


def revise_script(
    project_dir: Path,
    feedback: str,
    stages: Mapping[str, Stage],
    emitter: Emitter,
    provider_factory: ProviderFactory = get_provider,
    speech_rate: SpeechRate = settings_words_per_sec,
) -> list[str]:
    """Rewrite script.yaml following `feedback`; returns the stages marked stale."""
    notes = feedback.strip()
    if not notes:
        raise ScriptEditError("feedback_missing", "Hãy viết nhận xét về kịch bản trước khi nhờ Claude viết lại")
    script, items = _load(project_dir)
    project = config.load_project(project_dir)
    speed = _speed(script.words_per_sec, project.settings, speech_rate)
    stage = ScriptStage(provider_factory=provider_factory, speech_rate=speech_rate)
    started = time.monotonic()
    with emitter.stage_log(project_dir / LOG) as log:
        ctx = StageContext(project_dir, project, emitter, log)
        emitter.stage_start(NAME)
        emitter.log("info", f"Viết lại kịch bản theo nhận xét: {notes[:200]}")
        try:
            provider = provider_factory(project.settings)
            revised = _revise_parts(ctx, stage, provider, items, notes, script.target_duration_sec, speed)
        except (StageFailure, LLMError) as exc:
            emitter.stage_end(NAME, StageStatus.FAILED.value, elapsed_sec=round(time.monotonic() - started, 2))
            raise ScriptEditError(exc.code, exc.message, exc.retryable) from exc
        text = script_text(script.title, script.target_duration_sec, speed, revised)
        stage.back_up_previous(ctx, text)
        config.write_text_atomic(project_dir / SCRIPT, text)
        stale = _mark_rewritten(project_dir, project, stages)
        emitter.log("info", f"Kịch bản mới: {len(revised)} dòng (trước: {len(items)})")
        emitter.artifact(NAME, SCRIPT)
        emitter.stage_end(NAME, StageStatus.DONE.value, elapsed_sec=round(time.monotonic() - started, 2))
    return stale
