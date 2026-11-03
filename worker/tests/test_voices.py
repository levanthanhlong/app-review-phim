"""Voice catalog (for the app's voice picker) and voice previews."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from recap import voices
from recap.tts_engines import TTSError
from tests.test_tts import FakeEngine


def write_catalog(path: Path) -> Path:
    presets = {
        "Phụ": {"description": "Nữ · Nam · Phong cách đọc truyện", "gender": "female", "region": "Nam"},
        "Chính": {"description": "Nam · Bắc · Phong cách kể chuyện", "gender": "male", "region": "Bắc", "featured": 1},
    }
    path.write_text(json.dumps({"default_voice": "Chính", "presets": presets}), encoding="utf-8")
    return path


def test_vieneu_catalog_lists_featured_voices_first(tmp_path: Path) -> None:
    found = voices.vieneu_voices(write_catalog(tmp_path / "v.json"))

    assert [v.id for v in found] == ["Chính", "Phụ"]
    first = found[0]
    assert (first.engine, first.gender, first.region, first.featured) == ("vieneu", "male", "Bắc", True)
    assert first.description == "Nam · Bắc · Phong cách kể chuyện"
    assert found[1].featured is False


def test_missing_vieneu_catalog_gives_no_voices(tmp_path: Path) -> None:
    assert voices.vieneu_voices(tmp_path / "missing.json") == []


def test_installed_catalog_has_the_default_voice() -> None:
    found = voices.all_voices()

    assert voices.find_voice("vieneu", voices.DEFAULT_VOICE) is not None
    assert len([v for v in found if v.engine == "vieneu"]) >= 20
    assert {v.id for v in found if v.engine == "edge"} == {"vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"}


def test_find_voice_is_engine_specific() -> None:
    assert voices.find_voice("edge", voices.DEFAULT_VOICE) is None
    assert voices.find_voice("vieneu", "vi-VN-NamMinhNeural") is None


def test_check_voice_rejects_unknown_vieneu_voice() -> None:
    with pytest.raises(TTSError, match="Không có giọng"):
        voices.check_voice("vieneu", "Ai Đó")
    voices.check_voice("vieneu", voices.DEFAULT_VOICE)
    voices.check_voice("edge", "vi-VN-NamMinhNeural")


def test_check_voice_rejects_unknown_engine() -> None:
    with pytest.raises(TTSError, match="engine"):
        voices.check_voice("piper", "x")


def test_preview_writes_wav_with_the_chosen_voice(tmp_path: Path) -> None:
    engine = FakeEngine()
    out = tmp_path / "sub" / "preview.wav"

    duration = voices.make_preview("vieneu", voices.DEFAULT_VOICE, "+0%", "+0Hz", "Một hai ba.", out, {"vieneu": engine})

    assert out.is_file() and duration == pytest.approx(0.3)
    assert engine.calls == ["Một hai ba."]
    assert not list(out.parent.glob("*.tmp*"))


def test_preview_uses_default_text_when_empty(tmp_path: Path) -> None:
    engine = FakeEngine()

    voices.make_preview("vieneu", voices.DEFAULT_VOICE, "+0%", "+0Hz", "", tmp_path / "p.wav", {"vieneu": engine})

    assert engine.calls == [voices.PREVIEW_TEXT]


def test_preview_validates_voice_before_synthesis(tmp_path: Path) -> None:
    engine = FakeEngine()

    with pytest.raises(TTSError):
        voices.make_preview("vieneu", "Ai Đó", "+0%", "+0Hz", "x", tmp_path / "p.wav", {"vieneu": engine})

    assert engine.calls == []


def test_preview_failure_leaves_no_file(tmp_path: Path) -> None:
    out = tmp_path / "p.wav"

    with pytest.raises(TTSError):
        voices.make_preview("edge", "vi-VN-HoaiMyNeural", "+0%", "+0Hz", "x", out, {"edge": FakeEngine(fail_times=1)})

    assert not out.exists()


def test_measured_rate_is_used_and_scaled_by_tts_rate(tmp_path: Path) -> None:
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps({"vieneu:Chính": 4.0}), encoding="utf-8")

    assert voices.words_per_sec("vieneu", "Chính", "+0%", rates) == pytest.approx(4.0)
    assert voices.words_per_sec("vieneu", "Chính", "-25%", rates) == pytest.approx(3.0)


def test_unmeasured_voice_uses_default_rate(tmp_path: Path) -> None:
    assert voices.words_per_sec("vieneu", "Mới", "+0%", tmp_path / "missing.json") == voices.DEFAULT_WORDS_PER_SEC


def test_every_installed_voice_has_a_measured_rate() -> None:
    rates = json.loads(voices.RATES_FILE.read_text(encoding="utf-8"))

    missing = [f"{v.engine}:{v.id}" for v in voices.all_voices() if f"{v.engine}:{v.id}" not in rates]

    assert missing == [], "chạy tools/measure_voice_rates.py"
    assert all(2.0 < r < 6.0 for r in rates.values())


def test_catalog_entries_carry_their_rate() -> None:
    voice = voices.find_voice("vieneu", voices.DEFAULT_VOICE)

    assert voice is not None
    assert voice.words_per_sec == voices.words_per_sec("vieneu", voices.DEFAULT_VOICE, "+0%")
