"""Text-to-speech engines. Each writes a 44.1 kHz mono 16-bit wav to `out`, or raises TTSError."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import wave
from pathlib import Path
from typing import Protocol

SAMPLE_RATE = 44100
SAY_VOICE = "Linh"  # macOS Vietnamese voice
SAY_BASE_WPM = 175
_PERCENT = re.compile(r"^([+-]?\d+(?:\.\d+)?)%$")
MIN_RATE_PERCENT = -50.0  # ffmpeg atempo (used for VieNeu) supports 0.5x..2x
MAX_RATE_PERCENT = 100.0


class TTSError(Exception):
    pass


class Synth(Protocol):
    def __call__(self, text: str, voice: str, rate: str, pitch: str, out: Path) -> None: ...


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def rate_factor(rate: str) -> float:
    """`tts_rate` such as "+10%" / "-5%" as a speed multiplier (1.1 / 0.95)."""
    match = _PERCENT.match(rate.strip())
    if match is None:
        raise TTSError(f"tts_rate không hợp lệ: {rate!r} (ví dụ +10%, -5%)")
    percent = float(match.group(1))
    if not MIN_RATE_PERCENT <= percent <= MAX_RATE_PERCENT:
        raise TTSError(f"tts_rate {rate!r} ngoài khoảng {MIN_RATE_PERCENT:+.0f}%..{MAX_RATE_PERCENT:+.0f}%")
    return 1 + percent / 100


def to_wav(src: Path, out: Path, tempo: float = 1.0) -> None:
    """Convert to 44.1 kHz mono PCM; `tempo` != 1 changes speed without changing pitch."""
    tempo_args = ["-af", f"atempo={tempo:.6g}"] if tempo != 1.0 else []
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(src), *tempo_args,
           "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out)]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise TTSError(f"ffmpeg không chuyển được audio TTS: {proc.stderr.strip()}")


def edge_synth(text: str, voice: str, rate: str, pitch: str, out: Path) -> None:
    import edge_tts

    mp3 = out.with_suffix(".mp3")
    try:
        asyncio.run(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(mp3)))
        to_wav(mp3, out)
    except TTSError:
        raise
    except Exception as exc:  # edge_tts raises aiohttp/network/NoAudioReceived errors
        raise TTSError(f"edge-tts: {type(exc).__name__}: {exc}") from exc
    finally:
        mp3.unlink(missing_ok=True)


def _say_wpm(rate: str) -> int:
    return round(SAY_BASE_WPM * rate_factor(rate))


def say_synth(text: str, voice: str, rate: str, pitch: str, out: Path) -> None:
    """Offline fallback: macOS `say -v Linh` (voice/pitch of edge-tts do not apply)."""
    aiff = out.with_suffix(".aiff")
    try:
        proc = subprocess.run(
            ["say", "-v", SAY_VOICE, "-r", str(_say_wpm(rate)), "-o", str(aiff), text],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not aiff.is_file():
            raise TTSError(f"say lỗi: {proc.stderr.strip()}")
        to_wav(aiff, out)
    except FileNotFoundError as exc:
        raise TTSError("Không có lệnh say (chỉ có trên macOS)") from exc
    finally:
        if aiff.exists():
            os.unlink(aiff)
