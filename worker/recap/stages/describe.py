"""Stage `describe`: one short Vietnamese description per beat from its keyframe + dialogue.

Results are cached per beat (work/describe_cache.json, written after every beat), so a
cancelled or failed run resumes where it stopped and only changed beats are redone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Protocol

from recap import config
from recap.events import Emitter
from recap.models import DESCRIBED_ADAPTER, SCENES_ADAPTER, Beat, DescribedBeat, Project, Settings
from recap.ollama_vl import DescribeError, OllamaDescriber
from recap.runner import StageContext, StageFailure

SCENES = "work/scenes.json"
DESCRIBED = "work/scenes_described.json"
CACHE = "work/describe_cache.json"
MAX_WORDS = 40  # prompt asks for ≤30; hard cap guards against rambling output
NO_DIALOGUE = "(không có thoại)"

__all__ = ["DescribeError", "DescribeStage", "build_prompt", "clean_description"]


class Describer(Protocol):
    def describe(self, image: Path, prompt: str) -> str: ...

    def close(self) -> None: ...


DescriberFactory = Callable[[Settings, Emitter], Describer]


def _prompt_template() -> str:
    return resources.files("recap.prompts").joinpath("describe_scene.md").read_text(encoding="utf-8")


def build_prompt(dialogue: str) -> str:
    return _prompt_template().replace("{dialogue}", dialogue.strip() or NO_DIALOGUE)


def clean_description(raw: str) -> str:
    first_line = next((line for line in raw.strip().splitlines() if line.strip()), "")
    text = first_line.strip().strip('"“”\'').strip()
    return " ".join(text.split()[:MAX_WORDS])


def _cache_key(beat: Beat, keyframe: Path, model: str, prompt: str) -> str:
    # Content hash, not mtime: re-running `scenes` rewrites identical keyframes and must not invalidate them.
    image_hash = hashlib.sha256(keyframe.read_bytes()).hexdigest()
    payload = json.dumps([beat.id, beat.start, beat.end, beat.dialogue, image_hash, model, prompt], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _load_cache(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def ollama_factory(settings: Settings, emitter: Emitter) -> Describer:
    emitter.log("info", f"Ollama model: {settings.vl_model}")
    return OllamaDescriber(settings.vl_model)


class DescribeStage:
    name = "describe"

    def __init__(self, describer_factory: DescriberFactory = ollama_factory) -> None:
        self._factory = describer_factory

    def outputs(self, project: Project) -> list[str]:
        return [DESCRIBED]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        prompt_hash = hashlib.sha256(_prompt_template().encode()).hexdigest()[:12]
        return "|".join([config.file_signature(project_dir / SCENES), project.settings.vl_model, prompt_hash])

    def run(self, ctx: StageContext) -> Project:
        beats = self._load_beats(ctx)
        model = ctx.project.settings.vl_model
        keys = [_cache_key(b, ctx.path(f"work/{b.keyframe}"), model, build_prompt(b.dialogue)) for b in beats]
        cache = _load_cache(ctx.path(CACHE))
        todo = sum(1 for k in keys if k not in cache)
        ctx.emitter.log("info", f"{len(beats)} beat, {len(beats) - todo} đã có trong cache, cần mô tả {todo}")

        if todo:
            cache = self._describe_missing(ctx, beats, keys, cache)
        results = [DescribedBeat(**b.model_dump(), desc=cache[k]) for b, k in zip(beats, keys)]
        config.write_json_atomic(ctx.path(DESCRIBED), DESCRIBED_ADAPTER.dump_python(results, mode="json"))
        ctx.emitter.progress(self.name, len(beats), len(beats), beats[-1].id if beats else "")
        ctx.emitter.artifact(self.name, DESCRIBED)
        return ctx.project

    def _load_beats(self, ctx: StageContext) -> list[Beat]:
        scenes = ctx.path(SCENES)
        if not scenes.is_file():
            raise StageFailure("input_missing", "Thiếu scenes.json, hãy chạy lại stage scenes")
        beats = SCENES_ADAPTER.validate_json(scenes.read_bytes())
        missing = [b.keyframe for b in beats if not ctx.path(f"work/{b.keyframe}").is_file()]
        if missing:
            raise StageFailure("input_missing", f"Thiếu {len(missing)} keyframe (vd {missing[0]}), hãy chạy lại stage scenes")
        return beats

    def _describe_missing(
        self, ctx: StageContext, beats: list[Beat], keys: list[str], cache: dict[str, str]
    ) -> dict[str, str]:
        try:
            describer = self._factory(ctx.project.settings, ctx.emitter)
        except DescribeError as exc:
            raise StageFailure(exc.code, exc.message, exc.retryable) from exc
        try:
            for done, (beat, key) in enumerate(zip(beats, keys), start=1):
                if key not in cache:
                    raw = describer.describe(ctx.path(f"work/{beat.keyframe}"), build_prompt(beat.dialogue))
                    cache = {**cache, key: clean_description(raw)}
                    config.write_json_atomic(ctx.path(CACHE), cache)
                    ctx.emitter.raw(f"{beat.id}: {cache[key]}")
                ctx.emitter.progress(self.name, done, len(beats), beat.id)
        except DescribeError as exc:
            raise StageFailure(exc.code, exc.message, exc.retryable) from exc
        finally:
            describer.close()
        return cache
