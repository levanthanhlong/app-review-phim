"""VieNeu-TTS v3 Turbo engine: offline Vietnamese TTS (ONNX on CPU), 25 preset voices.

The model (~1 GB, in HF_HOME on the external SSD) is loaded on first use and kept until
`close()`, so one stage run pays the ~10 s load once. VieNeu has no speed control, so
`tts_rate` is applied afterwards with ffmpeg atempo; `tts_pitch` does not apply.
"""

from __future__ import annotations

import os
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from recap import config
from recap.tts_engines import TTSError, rate_factor, to_wav

MODEL_REPO = "pnnbao-ump/VieNeu-TTS-v3-Turbo"
MODEL_PROBE_FILE = "onnx_update/vieneu_prefill.onnx"
INT16_MAX = 32767


class VieneuModel(Protocol):
    sample_rate: int

    def infer(self, text: str, voice: str) -> Any: ...

    def close(self) -> None: ...


def _load_model() -> VieneuModel:
    config.configure_cache_env()  # before huggingface_hub is imported, so the model lands on the SSD
    from vieneu import Vieneu

    return Vieneu()


def model_is_cached() -> bool:
    """True when the model is already downloaded (no network needed to synthesize)."""
    config.configure_cache_env()
    from huggingface_hub import try_to_load_from_cache

    hf_home = os.environ.get("HF_HOME")
    cache_dir = Path(hf_home) / "hub" if hf_home else None
    return isinstance(try_to_load_from_cache(MODEL_REPO, MODEL_PROBE_FILE, cache_dir=cache_dir), str)


def _write_pcm16(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * INT16_MAX).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


class VieneuSynth:
    """`Synth` for VieNeu. `factory` is injectable so tests need no model."""

    def __init__(self, factory: Callable[[], VieneuModel] = _load_model) -> None:
        self._factory = factory
        self._model: VieneuModel | None = None

    def __call__(self, text: str, voice: str, rate: str, pitch: str, out: Path) -> None:
        tempo = rate_factor(rate)  # validate before paying for the model load
        model = self._get_model()
        try:
            audio = np.asarray(model.infer(text, voice=voice), dtype=np.float32).reshape(-1)
        except Exception as exc:  # unknown voice (ValueError) or inference errors
            raise TTSError(f"VieNeu: {type(exc).__name__}: {exc}") from exc
        if audio.size == 0:
            raise TTSError(f"VieNeu không tạo được âm thanh cho câu: {text!r}")

        raw = out.with_name(f"{out.stem}.raw.wav")
        try:
            _write_pcm16(raw, audio, model.sample_rate)
            to_wav(raw, out, tempo=tempo)
        finally:
            raw.unlink(missing_ok=True)

    def close(self) -> None:
        """Release the model (≈1 GB RAM); the next call loads it again."""
        if self._model is not None:
            self._model.close()
            self._model = None

    def _get_model(self) -> VieneuModel:
        if self._model is None:
            try:
                self._model = self._factory()
            except Exception as exc:  # download, onnxruntime or file errors from the library
                raise TTSError(f"Không nạp được VieNeu-TTS: {type(exc).__name__}: {exc}") from exc
        return self._model
