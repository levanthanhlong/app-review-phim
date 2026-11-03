"""VieNeu-TTS engine with a fake model (the real model is checked in an integration test)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from recap.tts_engines import TTSError, rate_factor, wav_duration
from recap.vieneu_tts import VieneuSynth, model_is_cached
from tests.conftest import HAS_FFMPEG

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not available")
RATE = 48000


class FakeModel:
    sample_rate = RATE

    def __init__(self, seconds: float = 1.0) -> None:
        self.seconds = seconds
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def infer(self, text: str, voice: str) -> np.ndarray:
        self.calls.append((text, voice))
        if voice == "Ai Đó":
            raise ValueError("Voice 'Ai Đó' not found.")
        t = np.arange(int(self.seconds * RATE)) / RATE
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def close(self) -> None:
        self.closed = True


class Factory:
    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.loads = 0

    def __call__(self) -> FakeModel:
        self.loads += 1
        return self.model


@pytest.mark.parametrize(("rate", "factor"), [("+0%", 1.0), ("+25%", 1.25), ("-10%", 0.9), (" +5.5% ", 1.055)])
def test_rate_factor_parses_percent(rate: str, factor: float) -> None:
    assert rate_factor(rate) == pytest.approx(factor)


@pytest.mark.parametrize("rate", ["fast", "10", "+200%", "-60%"])
def test_rate_factor_rejects_bad_values(rate: str) -> None:
    with pytest.raises(TTSError, match="tts_rate"):
        rate_factor(rate)


@needs_ffmpeg
def test_writes_44k_mono_wav(tmp_path: Path) -> None:
    model = FakeModel(seconds=1.0)
    out = tmp_path / "a.wav"

    VieneuSynth(Factory(model))("Xin chào.", "Hải Đăng", "+0%", "+0Hz", out)

    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (44100, 1, 2)
    assert wav_duration(out) == pytest.approx(1.0, abs=0.02)
    assert model.calls == [("Xin chào.", "Hải Đăng")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.wav"]


@needs_ffmpeg
def test_rate_changes_duration(tmp_path: Path) -> None:
    out = tmp_path / "fast.wav"

    VieneuSynth(Factory(FakeModel(seconds=2.0)))("x", "Hải Đăng", "+25%", "+0Hz", out)

    assert wav_duration(out) == pytest.approx(1.6, abs=0.03)


def test_model_is_loaded_once_and_released_on_close(tmp_path: Path) -> None:
    model = FakeModel(seconds=0.1)
    factory = Factory(model)
    synth = VieneuSynth(factory)

    for name in ("a", "b"):
        synth("x", "Hải Đăng", "+0%", "+0Hz", tmp_path / f"{name}.wav")
    synth.close()
    synth("x", "Hải Đăng", "+0%", "+0Hz", tmp_path / "c.wav")

    assert factory.loads == 2 and model.closed


def test_close_without_load_is_harmless() -> None:
    VieneuSynth(Factory(FakeModel())).close()


def test_unknown_voice_is_tts_error(tmp_path: Path) -> None:
    with pytest.raises(TTSError, match="VieNeu"):
        VieneuSynth(Factory(FakeModel()))("x", "Ai Đó", "+0%", "+0Hz", tmp_path / "a.wav")


def test_empty_audio_is_tts_error(tmp_path: Path) -> None:
    with pytest.raises(TTSError, match="không tạo được"):
        VieneuSynth(Factory(FakeModel(seconds=0)))("...", "Hải Đăng", "+0%", "+0Hz", tmp_path / "a.wav")


def test_bad_rate_fails_before_loading_model(tmp_path: Path) -> None:
    factory = Factory(FakeModel())

    with pytest.raises(TTSError):
        VieneuSynth(factory)("x", "Hải Đăng", "nhanh", "+0Hz", tmp_path / "a.wav")

    assert factory.loads == 0


def test_model_load_failure_is_tts_error(tmp_path: Path) -> None:
    def broken() -> FakeModel:
        raise RuntimeError("onnx boom")

    with pytest.raises(TTSError, match="nạp"):
        VieneuSynth(broken)("x", "Hải Đăng", "+0%", "+0Hz", tmp_path / "a.wav")


@pytest.mark.integration
@pytest.mark.skipif(not (HAS_FFMPEG and model_is_cached()), reason="needs ffmpeg + downloaded VieNeu model")
def test_real_vieneu_speaks_vietnamese(tmp_path: Path) -> None:
    out = tmp_path / "real.wav"
    synth = VieneuSynth()
    try:
        synth("Xin chào các bạn.", "Hải Đăng", "+0%", "+0Hz", out)
    finally:
        synth.close()
    assert 0.5 < wav_duration(out) < 5
