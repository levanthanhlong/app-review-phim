"""Stage `music`: AI background music, only when `settings.music_source == "ai"`.

1. Plan: the LLM splits the recap timeline into cues of one mood each and writes an English
   music prompt (plus an alternative) per cue. Prompts naming the film, its characters or an
   artist are rejected and the LLM is asked again (like the script stage). The plan is kept as
   long as its inputs do not change, so the user's edits and choices survive re-runs.
2. Generate 3 options per cue (see `music_options`), cached, with a fallback engine.
3. Write output/music_provenance.json (prompts, seeds, models, licenses, file hashes).

work/music/plan.json is what the app reads and edits (through `recap music-choose/-edit`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any

from recap import config
from recap.fit import TimelineEntry
from recap.llm import LLMError, LLMProvider, get_provider
from recap.models import SCENES_ADAPTER, Project, Settings
from recap.music_gen import Generator, default_generators
from recap.music_options import OptionsFailed, all_options, provenance, remove_unused
from recap.music_plan import MIN_CUE_SEC, Cue, banned_terms, cue_input, validate_cues
from recap.runner import StageContext, StageFailure
from recap.script_model import load_script, parse_response
from recap.stages.assemble import MANIFEST, SCENES, SCRIPT, project_timeline

MUSIC_DIR = "work/music"
PLAN = f"{MUSIC_DIR}/plan.json"
RAW_ANSWER = f"{MUSIC_DIR}/plan.raw.txt"
PROVENANCE = "output/music_provenance.json"
MAX_RETRIES = 2
OPTIONS_PER_CUE = 3
OFF = "off"

ProviderFactory = Callable[[Settings], LLMProvider]


def _template() -> str:
    return resources.files("recap.prompts").joinpath("plan_music.md").read_text(encoding="utf-8")


def plan_key(project_dir: Path, settings: Settings) -> str:
    """Identity of everything the cue plan (not the audio) is built from."""
    sigs = [config.file_signature(project_dir / f) for f in (SCRIPT, MANIFEST, SCENES)]
    prompt_hash = hashlib.sha256(_template().encode()).hexdigest()[:12]
    payload = "|".join([*sigs, prompt_hash, settings.llm_provider, settings.claude_model])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_plan(project_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((project_dir / PLAN).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def line_texts(project_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Narration text per line and, for original lines, the film dialogue of their beat."""
    _, items = load_script(project_dir / SCRIPT)
    beats = {b.id: b for b in SCENES_ADAPTER.validate_json((project_dir / SCENES).read_bytes())}
    texts = {i["id"]: i["text"] for i in items if i["type"] == "narrate"}
    dialogue = {i["id"]: beats[i["beat"]].dialogue for i in items if i["type"] == "original" and i["beat"] in beats}
    return texts, dialogue


def project_banned_terms(project_dir: Path, title: str) -> set[str]:
    texts, _ = line_texts(project_dir)
    return banned_terms(title, texts.values())


class MusicStage:
    name = "music"

    def __init__(
        self, provider_factory: ProviderFactory = get_provider, generators: Sequence[Generator] | None = None
    ) -> None:
        self._provider_factory = provider_factory
        self._generators = list(generators) if generators is not None else default_generators()

    def outputs(self, project: Project) -> list[str]:
        return [PLAN, PROVENANCE] if project.settings.music_source == "ai" else []

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        s = project.settings
        if s.music_source != "ai":
            return OFF
        return "|".join([plan_key(project_dir, s), s.music_model, config.file_signature(project_dir / PLAN)])

    def run(self, ctx: StageContext) -> Project:
        settings = ctx.project.settings
        if settings.music_source != "ai":
            ctx.emitter.log("info", "Không dùng nhạc AI (music_source khác ai), bỏ qua")
            return ctx.project
        generators = [g for g in self._generators if g.available()]
        if not generators:
            raise StageFailure("music_engine_missing", "Chưa cài Stable Audio 3 (tools/stable-audio-3) hay ACE-Step (tools/ACE-Step-1.5)")
        ctx.path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)
        key = plan_key(ctx.project_dir, settings)
        cues = self._cues(ctx, key)
        cues = self._generate(ctx, key, cues, generators)
        config.write_json_atomic(ctx.path(PROVENANCE), provenance(ctx.project.title, cues, generators))
        ctx.emitter.artifact(self.name, PLAN)
        ctx.emitter.artifact(self.name, PROVENANCE)
        return ctx.project

    # ---- plan ------------------------------------------------------------------------------

    def _cues(self, ctx: StageContext, key: str) -> list[dict[str, Any]]:
        """The saved cues when their inputs are unchanged (keeps user edits), otherwise a new LLM plan."""
        saved = load_plan(ctx.project_dir)
        if saved is not None and saved.get("plan_key") == key and not ctx.force:
            ctx.emitter.log("info", f"Dùng lại {len(saved['cues'])} phần nhạc đã chia")
            return list(saved["cues"])
        timeline = project_timeline(ctx)
        if not timeline:
            raise StageFailure("input_missing", "Kịch bản trống, hãy chạy lại stage script")
        texts, dialogue = line_texts(ctx.project_dir)
        cues = self._plan(ctx, timeline, cue_input(timeline, texts, dialogue), banned_terms(ctx.project.title, texts.values()))
        ctx.emitter.log("info", f"{len(cues)} phần nhạc: " + "; ".join(f"{c.id} {c.mood}" for c in cues))
        return [{**asdict(c), "chosen": 0, "round": 0, "options": []} for c in cues]

    def _plan(self, ctx: StageContext, timeline: list[TimelineEntry], digest: str, banned: set[str]) -> list[Cue]:
        """Ask the LLM; on invalid cues retry up to MAX_RETRIES times with the errors attached."""
        try:
            provider = self._provider_factory(ctx.project.settings)
        except LLMError as exc:
            raise StageFailure(exc.code, exc.message, exc.retryable) from exc
        total = timeline[-1].out_end
        base = _template().replace("{total_sec}", f"{total:.0f}").replace("{min_cue_sec}", str(MIN_CUE_SEC))
        prompt, answer, errors = base, "", []
        for attempt in range(MAX_RETRIES + 1):
            ctx.emitter.log("info", f"Chia phần nhạc: gọi Claude (lần {attempt + 1})")
            try:
                answer = provider.generate(prompt, digest)
            except LLMError as exc:
                raise StageFailure(exc.code, exc.message, exc.retryable) from exc
            cues, errors = self._parse(answer, timeline, banned)
            if not errors:
                return cues
            ctx.emitter.log("warning", f"Lần {attempt + 1} chưa hợp lệ: {'; '.join(errors[:3])}")
            prompt = base + "\n\nLần trả lời trước có các lỗi sau:\n- " + "\n- ".join(errors) + (
                "\nHãy viết lại TOÀN BỘ kết quả và sửa hết các lỗi trên."
            )
        config.write_text_atomic(ctx.path(RAW_ANSWER), answer)
        raise StageFailure(
            "music_plan_invalid",
            f"Claude chia phần nhạc sai sau {MAX_RETRIES + 1} lần ({errors[0]}). Bản thô lưu ở {RAW_ANSWER}",
            retryable=True,
        )

    @staticmethod
    def _parse(answer: str, timeline: list[TimelineEntry], banned: set[str]) -> tuple[list[Cue], list[str]]:
        try:
            data: Any = parse_response(answer)[0]
        except ValueError as exc:
            return [], [str(exc)]
        return validate_cues(data, timeline, banned)

    # ---- audio -----------------------------------------------------------------------------

    def _generate(
        self, ctx: StageContext, key: str, cues: list[dict[str, Any]], generators: Sequence[Generator]
    ) -> list[dict[str, Any]]:
        model = ctx.project.settings.music_model
        work = ctx.path("work")
        total = len(cues) * OPTIONS_PER_CUE
        finished = [0]  # options ready so far, for progress

        def progress(message: str) -> None:
            finished[0] += 1
            ctx.emitter.progress(self.name, finished[0], total, message)

        # The plan is saved first (no new LLM call after a failure); finished files are found again by name.
        config.write_json_atomic(ctx.path(PLAN), {"plan_key": key, "model": model, "cues": cues})
        try:
            options, fresh = all_options(work, cues, model, generators, ctx.log,
                                         lambda m: ctx.emitter.log("warning", m), progress)  # fmt: skip
        except OptionsFailed as exc:
            raise StageFailure("music_failed", str(exc), retryable=True) from exc
        done = [
            {**cue, "options": opts, "chosen": min(int(cue.get("chosen", 0)), len(opts) - 1)}
            for cue, opts in zip(cues, options)
        ]
        config.write_json_atomic(ctx.path(PLAN), {"plan_key": key, "model": model, "cues": done})
        remove_unused(work, done)
        ctx.emitter.log("info", f"Nhạc nền: {len(done)} phần × {OPTIONS_PER_CUE} bản, tạo mới {fresh}, dùng lại {total - fresh}")
        return done
