"""Stage `transcribe`: mlx-whisper on work/audio_16k.wav -> work/transcript.json."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from recap import config
from recap.models import TRANSCRIPT_ADAPTER, Project, Settings, TranscriptSegment, Word
from recap.runner import StageContext, StageFailure

AUDIO = "work/audio_16k.wav"
TRANSCRIPT = "work/transcript.json"
# Whisper's own "this is silence" rule; such segments are mostly hallucinated credits/ads.
NO_SPEECH_PROB_MAX = 0.6
AVG_LOGPROB_MIN = -1.0
HALLUCINATION_SILENCE_SEC = 2.0

ProgressFn = Callable[[float], None]  # fraction 0..1


class Transcriber(Protocol):
    def __call__(self, audio: Path, settings: Settings, on_progress: ProgressFn) -> dict[str, Any]: ...


def whisper_options(settings: Settings) -> dict[str, Any]:
    return {
        "path_or_hf_repo": settings.whisper_model,
        "language": None if settings.language == "auto" else settings.language,
        "task": settings.whisper_task,
        "word_timestamps": True,
        # Avoids the "same sentence repeated forever" failure on long films.
        "condition_on_previous_text": False,
        "hallucination_silence_threshold": HALLUCINATION_SILENCE_SEC,
    }


def _is_hallucination(raw: dict[str, Any]) -> bool:
    return raw.get("no_speech_prob", 0.0) > NO_SPEECH_PROB_MAX and raw.get("avg_logprob", 0.0) < AVG_LOGPROB_MIN


def segments_from_whisper(result: dict[str, Any]) -> list[TranscriptSegment]:
    kept = [s for s in result.get("segments", []) if s.get("text", "").strip() and not _is_hallucination(s)]
    return [
        TranscriptSegment(
            id=i,
            start=round(float(s["start"]), 3),
            end=round(float(s["end"]), 3),
            text=s["text"].strip(),
            words=[
                Word(start=round(float(w["start"]), 3), end=round(float(w["end"]), 3), word=w["word"].strip())
                for w in s.get("words") or []
            ],
        )
        for i, s in enumerate(kept, start=1)
    ]


@contextmanager
def _tqdm_progress(module: Any, on_progress: ProgressFn) -> Iterator[None]:
    """mlx_whisper reports progress through `tqdm.tqdm(total=frames)`; route it to `on_progress`."""

    class Bar:
        def __init__(self, total: int = 0, **_: Any) -> None:
            self.total, self.n = max(int(total), 1), 0

        def __enter__(self) -> Bar:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def update(self, n: int = 1) -> None:
            self.n += n
            on_progress(min(self.n / self.total, 1.0))

    original = module.tqdm
    module.tqdm = SimpleNamespace(tqdm=Bar)
    try:
        yield
    finally:
        module.tqdm = original


def mlx_transcriber(audio: Path, settings: Settings, on_progress: ProgressFn) -> dict[str, Any]:
    config.configure_cache_env()
    import mlx.core as mx  # heavy imports: only when the stage actually runs

    # The submodule, not the `mlx_whisper.transcribe` function the package re-exports.
    whisper_module = importlib.import_module("mlx_whisper.transcribe")

    try:
        with _tqdm_progress(whisper_module, on_progress):
            return whisper_module.transcribe(str(audio), verbose=False, **whisper_options(settings))
    finally:
        # Free the model so later stages (Ollama VL) get the RAM back when run in the same process.
        whisper_module.ModelHolder.model = None
        mx.clear_cache()


class TranscribeStage:
    name = "transcribe"

    def __init__(self, transcriber: Transcriber = mlx_transcriber) -> None:
        self._transcriber = transcriber

    def outputs(self, project: Project) -> list[str]:
        return [TRANSCRIPT]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        s = project.settings
        return "|".join([config.file_signature(project_dir / AUDIO), s.whisper_model, s.language, s.whisper_task])

    def run(self, ctx: StageContext) -> Project:
        audio = ctx.path(AUDIO)
        if not audio.is_file():
            raise StageFailure("input_missing", f"Thiếu {AUDIO}, hãy chạy lại stage ingest")
        duration = round(ctx.project.media.duration, 1) if ctx.project.media else 1.0
        ctx.emitter.log("info", f"Whisper model: {ctx.project.settings.whisper_model}")

        def report(fraction: float) -> None:
            ctx.emitter.progress(self.name, round(fraction * duration, 1), duration)

        try:
            result = self._transcriber(audio, ctx.project.settings, report)
        except (OSError, ValueError, RuntimeError) as exc:
            raise StageFailure("transcribe_failed", f"Whisper lỗi: {exc}", retryable=True) from exc

        segments = segments_from_whisper(result)
        ctx.emitter.log("info", f"Ngôn ngữ: {result.get('language')}, {len(segments)} câu thoại")
        config.write_json_atomic(ctx.path(TRANSCRIPT), TRANSCRIPT_ADAPTER.dump_python(segments, mode="json"))
        ctx.emitter.artifact(self.name, TRANSCRIPT)
        return ctx.project
