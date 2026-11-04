"""Stage `assemble`: align script + TTS to footage (work/timeline.json) and render the preview.

Rendering (with per-line caching) lives in `timeline_render`, shared with `export`.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from recap import config
from recap.branding import brand_signature
from recap.fit import TimelineEntry, build_timeline
from recap.models import SCENES_ADAPTER, Beat, Project
from recap.music import music_signature
from recap.render import PREVIEW
from recap.runner import StageContext, StageFailure
from recap.script_model import load_script, validate_items
from recap.cleanup import load_cleanup
from recap.timeline_render import (
    RENDER_VERSION,
    RenderJob,
    cleanup_signature,
    load_branding,
    load_music,
    render_timeline,
)

SCRIPT = "script.yaml"
SCENES = "work/scenes.json"
MANIFEST = "work/tts/manifest.json"
TIMELINE = "work/timeline.json"
PROXY = "work/proxy_480p.mp4"
PREVIEW_OUT = "output/recap_preview.mp4"
DEFAULT_FPS = 25.0
MAX_ERRORS_SHOWN = 3


def _load_beats(ctx: StageContext) -> dict[str, Beat]:
    path = ctx.path(SCENES)
    if not path.is_file():
        raise StageFailure("input_missing", "Thiếu scenes.json, hãy chạy lại stage scenes")
    return {b.id: b for b in SCENES_ADAPTER.validate_json(path.read_bytes())}


def _load_items(ctx: StageContext, beats: dict[str, Beat]) -> list[dict[str, Any]]:
    try:
        _, raw_items = load_script(ctx.path(SCRIPT))
    except ValueError as exc:
        raise StageFailure("script_invalid", str(exc)) from exc
    items, errors = validate_items(raw_items, beats, target_sec=0, check_duration=False)
    if errors:
        raise StageFailure("script_invalid", "script.yaml: " + "; ".join(errors[:MAX_ERRORS_SHOWN]))
    return items


def _load_tts(ctx: StageContext) -> dict[str, tuple[str, float]]:
    path = ctx.path(MANIFEST)
    if not path.is_file():
        raise StageFailure("input_missing", "Thiếu tts/manifest.json, hãy chạy lại stage tts")
    entries = json.loads(path.read_text(encoding="utf-8"))
    missing = [e["file"] for e in entries if not ctx.path(f"work/{e['file']}").is_file()]
    if missing:
        raise StageFailure("input_missing", f"Thiếu file giọng đọc {missing[0]}, hãy chạy lại stage tts")
    return {e["id"]: (e["file"], float(e["duration"])) for e in entries}


def project_timeline(ctx: StageContext) -> list[TimelineEntry]:
    """Script + TTS placed back to back (what `assemble` renders, also used by `music`)."""
    beats = _load_beats(ctx)
    items = _load_items(ctx, beats)
    tts = _load_tts(ctx)
    try:
        return build_timeline(items, beats, tts)
    except KeyError as exc:
        raise StageFailure("input_missing", f"Dòng {exc} chưa có giọng đọc, hãy chạy lại stage tts") from exc


def project_fps(project: Project) -> float:
    return (project.media.fps if project.media else None) or DEFAULT_FPS


class AssembleStage:
    name = "assemble"

    def outputs(self, project: Project) -> list[str]:
        return [TIMELINE, PREVIEW_OUT]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        files = (SCRIPT, MANIFEST, SCENES, PROXY)
        sigs = [config.file_signature(project_dir / f) for f in files]
        s = project.settings
        extras = [x for x in (music_signature(s, project_dir), brand_signature(s.brand_banner, s.brand_logo),
                             cleanup_signature(project_dir)) if x]  # fmt: skip
        return "|".join([*sigs, str(s.duck_volume), RENDER_VERSION, *extras])

    def run(self, ctx: StageContext) -> Project:
        music = load_music(ctx.project.settings, ctx.project_dir)
        timeline = project_timeline(ctx)
        config.write_json_atomic(ctx.path(TIMELINE), [asdict(e) for e in timeline])
        ctx.emitter.artifact(self.name, TIMELINE)
        counts = Counter(e.fit for e in timeline)
        ctx.emitter.log("info", f"fit: ok={counts['ok']} short={counts['short']} trimmed={counts['trimmed']}")

        brand = load_branding(ctx.project.settings, ctx.project_dir, PREVIEW)
        job = RenderJob(PREVIEW, ctx.path(PROXY), PREVIEW_OUT, project_fps(ctx.project), ctx.project.settings.duck_volume,
                        music=music, brand=brand, cleanup=load_cleanup(ctx.project_dir))  # fmt: skip
        render_timeline(ctx, self.name, timeline, job)
        total = timeline[-1].out_end if timeline else 0.0
        ctx.emitter.log("info", f"Video tóm tắt dài {total:.1f}s ({len(timeline)} dòng)")
        ctx.emitter.artifact(self.name, PREVIEW_OUT)
        return ctx.project
