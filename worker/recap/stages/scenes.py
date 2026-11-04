"""Stage `scenes`: detect shots on the proxy, group them into beats, extract one keyframe per beat."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from recap import config, media
from recap.beat_marks import keep_marks, load_marks, save_marks
from recap.beats import BeatPlan, Interval, dialogue_between, plan_beats, speech_intervals
from recap.models import SCENES_ADAPTER, TRANSCRIPT_ADAPTER, Beat, Project, Settings, TranscriptSegment
from recap.runner import StageContext, StageFailure

PROXY = "work/proxy_480p.mp4"
TRANSCRIPT = "work/transcript.json"
SCENES = "work/scenes.json"
KEYFRAMES_DIR = "keyframes"  # under work/
KEYFRAME_WIDTH = 512
DETECT_CHUNK_SEC = 30.0
TIME_DECIMALS = 3


def _detector(settings: Settings):  # type: ignore[no-untyped-def]  # scenedetect has no stubs
    from scenedetect import AdaptiveDetector, ContentDetector

    if settings.scene_detector == "content":
        return ContentDetector() if settings.scene_threshold is None else ContentDetector(threshold=settings.scene_threshold)
    if settings.scene_threshold is None:
        return AdaptiveDetector()
    return AdaptiveDetector(adaptive_threshold=settings.scene_threshold)


def detect_shots(video_path: Path, settings: Settings, on_progress: Callable[[float], None]) -> list[Interval]:
    """Shot boundaries in seconds. Processes the video in chunks so progress can be reported."""
    from scenedetect import SceneManager, open_video

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(_detector(settings))
    while manager.detect_scenes(video, duration=DETECT_CHUNK_SEC) > 0:
        on_progress(video.position.get_seconds())
    scenes = manager.get_scene_list(start_in_scene=True)
    return [(start.get_seconds(), end.get_seconds()) for start, end in scenes]


def _round(t: float) -> float:
    return round(t, TIME_DECIMALS)


def _beat_id(index: int) -> str:
    return f"B{index:04d}"


def build_beats(plans: list[BeatPlan], segments: list[TranscriptSegment]) -> list[Beat]:
    return [
        Beat(
            id=_beat_id(i),
            start=_round(p.start),
            end=_round(p.end),
            shots=[(_round(s), _round(e)) for s, e in p.shots],
            keyframe=f"{KEYFRAMES_DIR}/{_beat_id(i)}.jpg",
            dialogue=dialogue_between(segments, p.start, p.end),
        )
        for i, p in enumerate(plans, start=1)
    ]


class ScenesStage:
    name = "scenes"

    def outputs(self, project: Project) -> list[str]:
        return [SCENES, f"work/{KEYFRAMES_DIR}"]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        s = project.settings
        parts = [
            config.file_signature(project_dir / PROXY),
            config.file_signature(project_dir / TRANSCRIPT),
            f"{s.min_beat_sec}-{s.max_beat_sec}",
            s.scene_detector,
            str(s.scene_threshold),
        ]
        return "|".join(parts)

    def run(self, ctx: StageContext) -> Project:
        proxy, transcript_path = ctx.path(PROXY), ctx.path(TRANSCRIPT)
        for path, stage in ((proxy, "ingest"), (transcript_path, "transcribe")):
            if not path.is_file():
                raise StageFailure("input_missing", f"Thiếu {path.name}, hãy chạy lại stage {stage}")
        segments = TRANSCRIPT_ADAPTER.validate_json(transcript_path.read_bytes())
        settings = ctx.project.settings
        duration = round(ctx.project.media.duration, 1) if ctx.project.media else 0.0

        shots = detect_shots(
            proxy, settings, lambda sec: ctx.emitter.progress(self.name, round(sec, 1), duration, "Tách shot")
        )
        try:
            plans = plan_beats(shots, speech_intervals(segments), settings.min_beat_sec, settings.max_beat_sec)
        except ValueError as exc:
            raise StageFailure("invalid_settings", str(exc)) from exc
        beats = build_beats(plans, segments)
        ctx.emitter.log("info", f"{len(shots)} shot → {len(beats)} beat")

        self._write_keyframes(ctx, proxy, beats)
        self._keep_valid_marks(ctx, beats)
        config.write_json_atomic(ctx.path(SCENES), SCENES_ADAPTER.dump_python(beats, mode="json"))
        ctx.emitter.artifact(self.name, SCENES)
        return ctx.project

    @staticmethod
    def _keep_valid_marks(ctx: StageContext, beats: list[Beat]) -> None:
        """New beats can reuse old ids for other footage: keep only the marks of unchanged beats."""
        marks = load_marks(ctx.project_dir)
        if not marks:
            return
        try:
            old = SCENES_ADAPTER.validate_json(ctx.path(SCENES).read_bytes())
        except (FileNotFoundError, ValueError):
            old = []
        kept = keep_marks(marks, old, beats)
        if len(kept) != len(marks):
            ctx.emitter.log("warning", f"Chia cảnh lại: bỏ {len(marks) - len(kept)} đánh dấu của cảnh đã thay đổi")
        save_marks(ctx.project_dir, kept)

    def _write_keyframes(self, ctx: StageContext, proxy: Path, beats: list[Beat]) -> None:
        """Build all keyframes in keyframes.tmp/, then swap it in so old/new frames never mix."""
        final_dir = ctx.path(f"work/{KEYFRAMES_DIR}")
        tmp_dir = final_dir.with_name(f"{KEYFRAMES_DIR}.tmp")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir()
        try:
            for i, beat in enumerate(beats, start=1):
                out = tmp_dir / Path(beat.keyframe).name
                media.extract_frame(proxy, (beat.start + beat.end) / 2, out, KEYFRAME_WIDTH)
                ctx.emitter.progress(self.name, i, len(beats), f"Keyframe {beat.id}")
            shutil.rmtree(final_dir, ignore_errors=True)
            tmp_dir.rename(final_dir)
        except media.MediaError as exc:
            raise StageFailure("keyframe_failed", str(exc), retryable=True) from exc
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
