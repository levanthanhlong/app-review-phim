"""Measure each narration voice's speaking rate (words/second at tts_rate +0%).

Writes recap/data/voice_rates.json, used by the script stage to tell the LLM how many
words fit in the target duration. Re-run after adding voices or upgrading vieneu:

    .venv/bin/python tools/measure_voice_rates.py [--engines vieneu edge]

Rates include the short silences the engine adds around a sentence, exactly as the
pipeline measures TTS duration, so the estimate matches the real narration length.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from recap.tts_engines import Synth, TTSError, edge_synth, wav_duration
from recap.vieneu_tts import VieneuSynth
from recap.voices import RATES_FILE, all_voices

# Recap-style narration: varied length, punctuation and vocabulary; numbers written as words.
CALIBRATION = (
    "Giữa đêm mưa bão, người đàn ông lặng lẽ bước vào căn nhà bỏ hoang ở cuối làng.",
    "Hắn không hề biết rằng, chỉ ba ngày sau, mọi bí mật sẽ bị phơi bày.",
    "Cô gái quyết định rời khỏi thành phố, mang theo lá thư cuối cùng của mẹ.",
    "Nhưng mọi chuyện không đơn giản như vậy.",
    "Trong lúc cả đội đang tranh cãi, viên thanh tra phát hiện ra một chi tiết kỳ lạ trên tấm ảnh cũ.",
    "Hai mươi năm trôi qua, ngôi làng nhỏ ven sông vẫn giữ nguyên vẻ yên bình đến đáng sợ.",
    "Chàng trai liều mình lao vào đám cháy, cứu được đứa bé, rồi biến mất không một dấu vết.",
    "Và đó chính là lúc cuộc chiến thật sự bắt đầu.",
)


def measure(synth: Synth, voice: str, workdir: Path) -> float | None:
    words = seconds = 0.0
    for n, text in enumerate(CALIBRATION):
        out = workdir / f"{n}.wav"
        try:
            synth(text, voice, "+0%", "+0Hz", out)
        except TTSError as exc:
            print(f"  bỏ qua câu {n + 1}: {exc}", file=sys.stderr)
            continue
        words += len(text.split())
        seconds += wav_duration(out)
    return round(words / seconds, 2) if seconds else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", nargs="+", default=["vieneu", "edge"], choices=["vieneu", "edge"])
    args = parser.parse_args()

    rates = json.loads(RATES_FILE.read_text(encoding="utf-8")) if RATES_FILE.is_file() else {}
    vieneu = VieneuSynth()
    engines: dict[str, Synth] = {"vieneu": vieneu, "edge": edge_synth}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for voice in [v for v in all_voices() if v.engine in args.engines]:
                rate = measure(engines[voice.engine], voice.id, Path(tmp))
                print(f"{voice.engine:6s} {voice.id:24s} {rate} từ/giây", flush=True)
                if rate is not None:
                    rates[f"{voice.engine}:{voice.id}"] = rate
    finally:
        vieneu.close()

    RATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    RATES_FILE.write_text(json.dumps(dict(sorted(rates.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Đã ghi {RATES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
