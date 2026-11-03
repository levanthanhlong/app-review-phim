"""Transcribe stage with a fake Whisper (the real model is exercised by the acceptance run)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from recap import config
from recap.events import Emitter
from recap.models import Settings, StageStatus
from recap.runner import run_stage
from recap.stages.transcribe import TranscribeStage, segments_from_whisper, whisper_options


def fake_whisper_result() -> dict[str, Any]:
    return {
        "language": "vi",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": " Ngày xửa ngày xưa", "no_speech_prob": 0.01, "avg_logprob": -0.2,
             "words": [{"start": 0.0, "end": 0.8, "word": " Ngày", "probability": 0.9},
                       {"start": 0.8, "end": 2.0, "word": " xửa", "probability": 0.8}]},
            {"start": 2.0, "end": 3.0, "text": "   ", "no_speech_prob": 0.1, "avg_logprob": -0.3, "words": []},
            {"start": 3.0, "end": 6.0, "text": " Hãy đăng ký kênh", "no_speech_prob": 0.9, "avg_logprob": -1.5,
             "words": []},
            {"start": 6.0, "end": 8.0, "text": " có một con cóc", "no_speech_prob": 0.05, "avg_logprob": -0.4},
        ],
    }  # fmt: skip


def test_segments_drop_empty_and_likely_hallucinated() -> None:
    segments = segments_from_whisper(fake_whisper_result())
    assert [s.text for s in segments] == ["Ngày xửa ngày xưa", "có một con cóc"]
    assert [s.id for s in segments] == [1, 2]
    assert segments[0].words[1].word == "xửa"
    assert segments[0].speaker is None


def test_whisper_options_from_settings() -> None:
    assert whisper_options(Settings())["language"] is None
    opts = whisper_options(Settings(language="ja", whisper_task="translate"))
    assert opts["language"] == "ja" and opts["task"] == "translate"
    assert opts["word_timestamps"] is True


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, audio: Path, settings: Settings, on_progress: Any) -> dict[str, Any]:
        self.calls += 1
        on_progress(0.5)
        on_progress(1.0)
        return fake_whisper_result()


def _ingested_project(tmp_path: Path) -> Path:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)
    (project_dir / "work/audio_16k.wav").write_bytes(b"RIFF fake")
    project = config.load_project(project_dir)
    done = project.stages["ingest"].model_copy(update={"status": StageStatus.DONE})
    config.save_project(project_dir, project.with_stage("ingest", done))
    return project_dir


def test_stage_writes_transcript_and_skips_second_run(tmp_path: Path) -> None:
    # Arrange
    project_dir = _ingested_project(tmp_path)
    fake = FakeTranscriber()
    stages = {"transcribe": TranscribeStage(transcriber=fake)}
    out = io.StringIO()

    # Act
    ok1 = run_stage(project_dir, "transcribe", stages, Emitter(stream=out, raw_stream=io.StringIO()))
    ok2 = run_stage(project_dir, "transcribe", stages, Emitter(stream=out, raw_stream=io.StringIO()))

    # Assert
    assert ok1 and ok2 and fake.calls == 1
    data = json.loads((project_dir / "work/transcript.json").read_text())
    assert data[0] == {
        "id": 1, "start": 0.0, "end": 2.0, "text": "Ngày xửa ngày xưa", "speaker": None,
        "words": [{"start": 0.0, "end": 0.8, "word": "Ngày"}, {"start": 0.8, "end": 2.0, "word": "xửa"}],
    }  # fmt: skip
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert any(e["type"] == "progress" and e["done"] == 0.5 for e in events)
    assert events[-1]["skipped"] is True


def test_changing_whisper_model_invalidates_transcript(tmp_path: Path) -> None:
    project_dir = _ingested_project(tmp_path)
    fake = FakeTranscriber()
    stages = {"transcribe": TranscribeStage(transcriber=fake)}
    quiet = Emitter(stream=io.StringIO(), raw_stream=io.StringIO())
    run_stage(project_dir, "transcribe", stages, quiet)

    project = config.load_project(project_dir)
    new_settings = project.settings.model_copy(update={"whisper_model": "mlx-community/whisper-small"})
    config.save_project(project_dir, project.model_copy(update={"settings": new_settings}))
    run_stage(project_dir, "transcribe", stages, quiet)

    assert fake.calls == 2
