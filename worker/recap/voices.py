"""Narration voice catalog and previews, for the app's voice picker.

The VieNeu catalog is read from the JSON shipped inside the `vieneu` package, so listing
voices is instant (no model load, no network).
"""

from __future__ import annotations

import importlib.util
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from recap.models import Settings
from recap.script_model import WORDS_PER_SEC
from recap.tts_engines import Synth, TTSError, edge_synth, rate_factor, wav_duration
from recap.vieneu_tts import VieneuSynth

ENGINES = ("vieneu", "edge")
DEFAULT_ENGINE = "vieneu"
DEFAULT_VOICE = "Hải Đăng"
PREVIEW_TEXT = "Xin chào, đây là giọng đọc thử cho video tóm tắt phim của bạn. Bạn thấy giọng này có hợp không?"
VIENEU_CATALOG = "assets/voices_v3_turbo.json"
RATES_FILE = Path(__file__).parent / "data" / "voice_rates.json"  # from tools/measure_voice_rates.py
DEFAULT_WORDS_PER_SEC = WORDS_PER_SEC  # for voices that were never measured


@dataclass(frozen=True)
class Voice:
    engine: str
    id: str  # value for settings.voice
    description: str
    gender: str  # male | female
    region: str  # Bắc | Trung | Nam
    featured: bool  # editors' pick: show first
    words_per_sec: float  # measured speaking rate at tts_rate +0%


# (id, description, gender)
_EDGE_VOICES = (
    ("vi-VN-NamMinhNeural", "Nam · Bắc · edge-tts (cần mạng)", "male"),
    ("vi-VN-HoaiMyNeural", "Nữ · Bắc · edge-tts (cần mạng)", "female"),
)


def _load_rates(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def words_per_sec(engine: str, voice: str, rate: str, rates_path: Path = RATES_FILE) -> float:
    """Expected narration speed of a voice: its measured rate scaled by `tts_rate`."""
    base = _load_rates(rates_path).get(f"{engine}:{voice}", DEFAULT_WORDS_PER_SEC)
    return base * rate_factor(rate)


def settings_words_per_sec(settings: Settings) -> float:
    """Narration speed of a project's voice (measured rate × tts_rate). Raises TTSError on a bad tts_rate."""
    return words_per_sec(settings.tts_engine, settings.voice, settings.tts_rate)


def _vieneu_catalog_path() -> Path | None:
    spec = importlib.util.find_spec("vieneu")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations))) / VIENEU_CATALOG


def vieneu_voices(path: Path | None = None) -> list[Voice]:
    """VieNeu preset voices, editors' picks first (in their rank), then file order."""
    path = path or _vieneu_catalog_path()
    if path is None or not path.is_file():
        return []
    presets = json.loads(path.read_text(encoding="utf-8")).get("presets", {})
    ranked = sorted(presets.items(), key=lambda kv: (kv[1].get("featured") is None, kv[1].get("featured") or 0))
    rates = _load_rates(RATES_FILE)
    return [
        Voice(
            "vieneu", name, v.get("description", ""), v.get("gender", ""), v.get("region", ""),
            v.get("featured") is not None, rates.get(f"vieneu:{name}", DEFAULT_WORDS_PER_SEC),
        )  # fmt: skip
        for name, v in ranked
    ]


def edge_voices() -> list[Voice]:
    rates = _load_rates(RATES_FILE)
    return [
        Voice("edge", vid, desc, gender, "Bắc", False, rates.get(f"edge:{vid}", DEFAULT_WORDS_PER_SEC))
        for vid, desc, gender in _EDGE_VOICES
    ]


def all_voices() -> list[Voice]:
    return [*vieneu_voices(), *edge_voices()]


def find_voice(engine: str, voice_id: str) -> Voice | None:
    return next((v for v in all_voices() if v.engine == engine and v.id == voice_id), None)


def check_voice(engine: str, voice_id: str) -> None:
    if engine not in ENGINES:
        raise TTSError(f"Không hỗ trợ TTS engine {engine!r} (chọn: {', '.join(ENGINES)})")
    if engine == "vieneu" and find_voice(engine, voice_id) is None:
        raise TTSError(f"Không có giọng {voice_id!r} trong VieNeu-TTS. Chạy `python -m recap voices` để xem danh sách")


def default_engines() -> dict[str, Synth]:
    return {"vieneu": VieneuSynth(), "edge": edge_synth}


def make_preview(
    engine: str,
    voice: str,
    rate: str,
    pitch: str,
    text: str,
    out: Path,
    engines: Mapping[str, Synth] | None = None,
) -> float:
    """Synthesize a sample with the given voice to `out` (atomic). Returns its duration in seconds."""
    check_voice(engine, voice)
    synth = (engines or default_engines())[engine]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.tmp.wav")
    try:
        synth(text.strip() or PREVIEW_TEXT, voice, rate, pitch, tmp)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
        close = getattr(synth, "close", None)
        if close is not None:
            close()
    return wav_duration(out)
