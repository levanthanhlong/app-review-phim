"""Render a timeline to one video file (used by `assemble` for the preview and `export` for the final).

Every timeline entry is rendered to work/render/<profile>/<hash>.mkv; unchanged entries are
reused, so editing one line only re-renders that line before the (cheap) final join.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from recap import config, media
from recap.branding import (
    BrandingError,
    BrandLayout,
    brand_layout,
    build_frame,
    check_image,
    image_size,
)
from recap.fit import TimelineEntry
from recap.models import Settings
from recap.music import MusicBed, MusicError, music_bed, music_filter, music_inputs
from recap.render import RenderProfile, concat_args, item_args
from recap.runner import StageContext, StageFailure

RENDER_DIR = "work/render"
RENDER_VERSION = "1"  # bump when render.py output changes, to invalidate cached clips


@dataclass(frozen=True)
class RenderJob:
    profile: RenderProfile
    source: Path  # video the segments are cut from (proxy or original film)
    output: str  # project-relative path of the joined video
    fps: float
    duck: float
    audio_stream: int = 0  # index among the source's audio streams
    music: MusicBed | None = None  # background music mixed in at the join
    brand: tuple[BrandLayout, Path] | None = None  # (layout, frame PNG) of the channel branding


BRAND_DIR = "work/brand"


def load_branding(settings: Settings, project_dir: Path, profile: RenderProfile) -> tuple[BrandLayout, Path] | None:
    """Layout and frame image of the channel branding at this profile's size (None = no branding)."""
    if not settings.brand_banner:
        return None
    try:
        banner = check_image(settings.brand_banner)
        logo = check_image(settings.brand_logo) if settings.brand_logo else None
    except BrandingError as exc:
        raise StageFailure("branding_missing", str(exc)) from exc
    layout = brand_layout(image_size(banner), profile.width, profile.height)
    return layout, build_frame(banner, logo, layout, project_dir / BRAND_DIR / f"{profile.name}.png")


def load_music(settings: Settings, project_dir: Path) -> MusicBed | None:
    """The project's background music, checked before any rendering starts."""
    try:
        return music_bed(settings, project_dir)
    except MusicError as exc:
        raise StageFailure(exc.code, str(exc)) from exc


def _render_key(entry: TimelineEntry, edges: tuple[bool, bool], extra: list[str]) -> str:
    data = asdict(entry)
    data["duration"] = round(entry.out_end - entry.out_start, 3)
    for moving in ("out_start", "out_end"):  # position in the recap does not change the clip
        data.pop(moving)
    payload = json.dumps([data, edges, extra, RENDER_VERSION], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def render_timeline(ctx: StageContext, stage: str, timeline: list[TimelineEntry], job: RenderJob) -> None:
    clips = _render_entries(ctx, stage, timeline, job)
    _join(ctx, clips, job, timeline)


def _render_entries(ctx: StageContext, stage: str, timeline: list[TimelineEntry], job: RenderJob) -> list[Path]:
    render_dir = ctx.path(f"{RENDER_DIR}/{job.profile.name}")
    render_dir.mkdir(parents=True, exist_ok=True)
    if not job.source.is_file():
        raise StageFailure("input_missing", f"Thiếu video nguồn {job.source}")
    extra = [job.profile.name, str(job.fps), str(job.duck), config.file_signature(job.source), f"a{job.audio_stream}"]
    if job.brand is not None:  # content hash: the frame is redrawn on every run
        extra.append("brand:" + hashlib.sha256(job.brand[1].read_bytes()).hexdigest()[:16])

    clips: list[Path] = []
    fresh = 0
    for i, entry in enumerate(timeline):
        prev_narrate = i > 0 and timeline[i - 1].type == "narrate"
        next_narrate = i + 1 < len(timeline) and timeline[i + 1].type == "narrate"
        edges = (not prev_narrate, not next_narrate)
        tts_path = ctx.path(f"work/{entry.tts_file}") if entry.tts_file else None
        # content hash: an identical wav written again must not force a re-render
        tts_sig = hashlib.sha256(tts_path.read_bytes()).hexdigest() if tts_path else ""
        out = render_dir / f"{_render_key(entry, edges, [*extra, tts_sig])}.mkv"
        if not out.is_file():
            _render_one(ctx, entry, tts_path, edges, out, job)
            fresh += 1
        clips.append(out)
        ctx.emitter.progress(stage, i + 1, len(timeline), entry.id)

    ctx.emitter.log("info", f"Render: {fresh} dòng mới, {len(timeline) - fresh} dòng dùng cache")
    keep = {c.name for c in clips}
    for stale in render_dir.glob("*.mkv"):
        if stale.name not in keep:
            stale.unlink()
    return clips


def _render_one(
    ctx: StageContext,
    entry: TimelineEntry,
    tts_path: Path | None,
    edges: tuple[bool, bool],
    out: Path,
    job: RenderJob,
) -> None:
    tmp = out.with_name(f"{out.stem}.tmp.mkv")
    args = item_args(
        entry, job.source, tts_path, job.profile, job.fps, job.duck, edges[0], edges[1], tmp, job.audio_stream,
        job.brand,
    )
    try:
        media.run_ffmpeg(args, total_sec=entry.out_end - entry.out_start, on_progress=lambda _: None, log=ctx.log)
        os.replace(tmp, out)
    except media.MediaError as exc:
        raise StageFailure("render_failed", f"{entry.id}: {exc}", retryable=True) from exc
    finally:
        tmp.unlink(missing_ok=True)


def _join(ctx: StageContext, clips: list[Path], job: RenderJob, timeline: list[TimelineEntry]) -> None:
    list_file = ctx.path(f"{RENDER_DIR}/{job.profile.name}/concat.txt")
    # The concat demuxer resolves relative paths against the list file, so write absolute
    # ones; a single quote is written as '\'' inside the quoted path.
    lines = ["file '{}'".format(c.resolve().as_posix().replace("'", "'\\''")) for c in clips]
    config.write_text_atomic(list_file, "\n".join(lines) + "\n")
    out = ctx.path(job.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.tmp{out.suffix}")
    try:
        media.run_ffmpeg(_join_args(list_file, tmp, job, timeline), total_sec=1.0, on_progress=lambda _: None, log=ctx.log)
        os.replace(tmp, out)
    except media.MediaError as exc:
        raise StageFailure("render_failed", f"Ghép video lỗi: {exc}", retryable=True) from exc
    finally:
        tmp.unlink(missing_ok=True)


def _join_args(list_file: Path, out: Path, job: RenderJob, timeline: list[TimelineEntry]) -> list[str]:
    if job.music is None or not timeline:
        return concat_args(list_file, out)
    total = timeline[-1].out_end
    return concat_args(list_file, out, music_inputs(job.music, total), music_filter(job.music, timeline, first_input=1))
