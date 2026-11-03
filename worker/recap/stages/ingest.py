"""Stage `ingest`: probe the source, build a 480p proxy and a 16 kHz mono wav in one ffmpeg pass."""

from __future__ import annotations

import os
from pathlib import Path

from recap import config, media
from recap.models import MediaInfo, Project
from recap.runner import StageContext, StageFailure

PROXY = "work/proxy_480p.mp4"
AUDIO = "work/audio_16k.wav"
PROXY_HEIGHT = 480
PROXY_CRF = 28
WHISPER_SAMPLE_RATE = 16000


def _tmp(path: Path) -> Path:
    """`proxy_480p.mp4` -> `proxy_480p.tmp.mp4` (extension kept so ffmpeg picks the muxer)."""
    return path.with_name(f"{path.stem}.tmp{path.suffix}")


def _source(project_dir: Path) -> Path:
    try:
        link = config.source_link(project_dir)
    except config.ProjectError as exc:
        raise StageFailure(exc.code, exc.message) from exc
    if not link.exists():
        raise StageFailure("source_missing", f"File phim gốc không còn tồn tại: {os.readlink(link)}")
    return link


def _audio_track(project: Project, info: MediaInfo) -> int:
    chosen = project.settings.audio_track
    if chosen is None:
        default = info.default_audio_track()
        if default is None:
            raise StageFailure("no_audio", "Phim không có track âm thanh")
        return default
    if not 0 <= chosen < len(info.audio_tracks):
        count = len(info.audio_tracks)
        raise StageFailure("audio_track_invalid", f"audio_track={chosen} không tồn tại (phim có {count} track)")
    return chosen


def _ffmpeg_args(source: Path, track: int, proxy_tmp: Path, audio_tmp: Path) -> list[str]:
    return [
        "-i", str(source),
        # output 1: proxy for analysis + preview
        "-map", "0:v:0", "-map", f"0:a:{track}",
        "-vf", f"scale=-2:'min({PROXY_HEIGHT},ih)'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(PROXY_CRF), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart",
        str(proxy_tmp),
        # output 2: audio for whisper
        "-map", f"0:a:{track}", "-vn", "-ac", "1", "-ar", str(WHISPER_SAMPLE_RATE), "-c:a", "pcm_s16le",
        str(audio_tmp),
    ]  # fmt: skip


class IngestStage:
    name = "ingest"

    def outputs(self, project: Project) -> list[str]:
        return [PROXY, AUDIO]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        source = _source(project_dir).resolve()
        stat = source.stat()
        track = project.settings.audio_track
        if track is None and project.media is not None:
            track = project.media.default_audio_track()
        return f"{source}|{stat.st_size}|{stat.st_mtime_ns}|a{track}"

    def run(self, ctx: StageContext) -> Project:
        source = _source(ctx.project_dir)
        try:
            info = media.probe(source)
        except media.MediaError as exc:
            raise StageFailure("probe_failed", str(exc)) from exc
        if info.height is None:
            raise StageFailure("no_video", "Phim không có track hình")
        track = _audio_track(ctx.project, info)
        fps = info.fps or 0.0
        ctx.emitter.log("info", f"{info.width}x{info.height} @ {fps:.3f}fps, {info.duration:.1f}s, audio track {track}")

        finals = [ctx.path(PROXY), ctx.path(AUDIO)]
        tmps = [_tmp(p) for p in finals]
        total = round(info.duration, 1)
        try:
            media.run_ffmpeg(
                _ffmpeg_args(source, track, tmps[0], tmps[1]),
                total_sec=info.duration,
                on_progress=lambda sec: ctx.emitter.progress(self.name, round(sec, 1), total),
                log=ctx.log,
            )
            for tmp, final in zip(tmps, finals):
                os.replace(tmp, final)
        except media.MediaError as exc:
            raise StageFailure("ffmpeg_failed", str(exc), retryable=True) from exc
        finally:
            for tmp in tmps:
                tmp.unlink(missing_ok=True)

        ctx.emitter.progress(self.name, total, total)
        for rel in (PROXY, AUDIO):
            ctx.emitter.artifact(self.name, rel)
        return ctx.project.model_copy(update={"media": info})
