"""Stage `export`: recap.srt, the final render from the original film, and a burned-in-subtitle copy.

- output/recap.srt         narration subtitles timed to the TTS (see `subtitles`)
- output/recap.mp4         the assemble timeline re-rendered from the source film, up to
                           1080p (never upscaled), with the project's audio track
- output/recap_subbed.mp4  recap.mp4 with the subtitles burned in (Pillow images + overlay)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from recap import config, media
from recap.branding import brand_signature
from recap.fit import TimelineEntry, timeline_from_json
from recap.models import MediaInfo, Project
from recap.music import music_signature
from recap.render import RenderProfile
from recap.runner import StageContext, StageFailure
from recap.script_model import load_script
from recap.stages.assemble import MANIFEST, SCRIPT, TIMELINE, project_fps
from recap.subtitle_images import SubtitleError, overlay_concat, resolve_font
from recap.subtitles import Cue, build_cues, to_srt
from recap.cleanup import load_cleanup
from recap.timeline_render import (
    RENDER_VERSION,
    RenderJob,
    cleanup_signature,
    load_branding,
    load_music,
    render_timeline,
)

SRT_OUT = "output/recap.srt"
FINAL_OUT = "output/recap.mp4"
SUBBED_OUT = "output/recap_subbed.mp4"
SUBS_DIR = "work/subs"
SUBS_VERSION = "2"  # bump when subtitle layout/style changes
FINAL_HEIGHT = 1080
FINAL_BITRATE = "10M"  # 8–12 Mbps for 1080p uploads
FINAL_MAX_BITRATE = "12M"
FINAL_CRF = 20
FINAL_PRESET = "medium"
ASPECT = 16 / 9


def final_profile(media_info: MediaInfo | None) -> RenderProfile:
    """Always 1080p 16:9 at FINAL_BITRATE: smaller sources are scaled up (lanczos, light sharpening),
    since the platforms favour 1080p uploads. `media_info` is kept for callers (resolution checks)."""
    height = FINAL_HEIGHT
    width = round(height * ASPECT)
    return RenderProfile("final", width, height, crf=FINAL_CRF, preset=FINAL_PRESET,
                         video_bitrate=FINAL_BITRATE, max_bitrate=FINAL_MAX_BITRATE, sharpen=True)  # fmt: skip


def _audio_stream(project: Project) -> int:
    chosen = project.settings.audio_track
    if chosen is None and project.media is not None:
        chosen = project.media.default_audio_track()
    return chosen or 0


def _subbed_args(video: Path, listing: Path, profile: RenderProfile, out: Path) -> list[str]:
    return [
        "-i", str(video), "-f", "concat", "-safe", "0", "-i", str(listing),
        "-filter_complex", "[1:v]format=rgba[subs];[0:v][subs]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[v]",
        "-map", "[v]", "-map", "0:a",
        *profile.video_codec_args(),
        "-c:a", "copy", "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip


class ExportStage:
    name = "export"

    def outputs(self, project: Project) -> list[str]:
        return [SRT_OUT, FINAL_OUT, SUBBED_OUT]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        s = project.settings
        try:
            source = config.source_link(project_dir).resolve()
        except config.ProjectError:
            source = project_dir / "source.missing"
        profile = final_profile(project.media)
        sigs = [config.file_signature(project_dir / f) for f in (TIMELINE, SCRIPT, MANIFEST)]
        fields = [f"{profile.width}x{profile.height}", s.subtitle_font, str(s.duck_volume), str(_audio_stream(project))]
        extras = [x for x in (music_signature(s, project_dir), brand_signature(s.brand_banner, s.brand_logo),
                             cleanup_signature(project_dir)) if x]  # fmt: skip
        return "|".join([*sigs, config.file_signature(source), *fields, RENDER_VERSION, SUBS_VERSION, *extras])

    def run(self, ctx: StageContext) -> Project:
        try:
            font = resolve_font(ctx.project.settings.subtitle_font)
        except SubtitleError as exc:
            raise StageFailure("font_missing", str(exc)) from exc
        music = load_music(ctx.project.settings, ctx.project_dir)
        timeline = self._load_timeline(ctx)
        try:
            _, items = load_script(ctx.path(SCRIPT))
        except ValueError as exc:
            raise StageFailure("script_invalid", str(exc)) from exc

        cues = build_cues(timeline, {i["id"]: i["text"] for i in items if i["type"] == "narrate"})
        ctx.path(SRT_OUT).parent.mkdir(parents=True, exist_ok=True)
        config.write_text_atomic(ctx.path(SRT_OUT), to_srt(cues))
        ctx.emitter.log("info", f"Phụ đề: {len(cues)} đoạn")
        ctx.emitter.artifact(self.name, SRT_OUT)

        profile = final_profile(ctx.project.media)
        source = config.source_link(ctx.project_dir)
        ctx.emitter.log("info", f"Render bản cuối {profile.width}x{profile.height} từ phim gốc")
        brand = load_branding(ctx.project.settings, ctx.project_dir, profile)
        job = RenderJob(profile, source, FINAL_OUT, project_fps(ctx.project), ctx.project.settings.duck_volume,
                        _audio_stream(ctx.project), music, brand, load_cleanup(ctx.project_dir))  # fmt: skip
        render_timeline(ctx, self.name, timeline, job)
        ctx.emitter.artifact(self.name, FINAL_OUT)

        total = timeline[-1].out_end if timeline else 0.0
        bottom = brand[0].bottom if brand else 0
        listing = self._subtitle_images(ctx, cues, total, profile, font, bottom)
        self._burn_subtitles(ctx, listing, profile, total)
        ctx.emitter.artifact(self.name, SUBBED_OUT)
        return ctx.project

    def _load_timeline(self, ctx: StageContext) -> list[TimelineEntry]:
        path = ctx.path(TIMELINE)
        if not path.is_file():
            raise StageFailure("input_missing", "Thiếu timeline.json, hãy chạy lại stage assemble")
        timeline = timeline_from_json(json.loads(path.read_text(encoding="utf-8")))
        if not timeline:
            raise StageFailure("input_missing", "timeline.json trống, hãy chạy lại stage assemble")
        return timeline

    @staticmethod
    def _subtitle_images(
        ctx: StageContext, cues: list[Cue], total: float, profile: RenderProfile, font: Path, bottom: int
    ) -> Path:
        folder = ctx.path(SUBS_DIR)
        shutil.rmtree(folder, ignore_errors=True)  # cue numbering changes whenever the script does
        return overlay_concat(cues, total, profile.width, profile.height, font, folder, bottom)

    def _burn_subtitles(self, ctx: StageContext, listing: Path, profile: RenderProfile, total: float) -> None:
        out = ctx.path(SUBBED_OUT)
        tmp = out.with_name(f"{out.stem}.tmp{out.suffix}")

        def progress(sec: float) -> None:
            ctx.emitter.progress(self.name, round(sec, 1), round(total, 1), "Gắn phụ đề")

        try:
            media.run_ffmpeg(_subbed_args(ctx.path(FINAL_OUT), listing, profile, tmp), total, progress, ctx.log)
            tmp.replace(out)
        except media.MediaError as exc:
            raise StageFailure("render_failed", f"Gắn phụ đề lỗi: {exc}", retryable=True) from exc
        finally:
            tmp.unlink(missing_ok=True)
