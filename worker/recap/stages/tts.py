"""Stage `tts`: one wav per narration line, cached by hash(engine, voice, rate, pitch, text).

Writes work/tts/manifest.json: [{id, file, duration, engine}] (file relative to work/).
`settings.tts_engine` picks the engine:
- vieneu (default): offline, so a failure is reported at once (no retry, no fallback).
- edge: tried EDGE_ATTEMPTS times (network); then macOS `say` is used and a warning is
  emitted. Lines that fell back are retried with edge-tts on the next run.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from recap import config
from recap.models import Project, Settings
from recap.runner import StageContext, StageFailure
from recap.script_model import load_script
from recap.stages.script import SPEED_TOLERANCE, SpeechRate, format_rate
from recap.tts_engines import Synth, TTSError, rate_factor, say_synth, wav_duration
from recap.voices import check_voice, default_engines, settings_words_per_sec

SCRIPT = "script.yaml"
TTS_DIR = "work/tts"
MANIFEST = "work/tts/manifest.json"
EDGE_ATTEMPTS = 3
RETRY_DELAY_SEC = 2.0


def _file_name(engine: str, text: str, settings: Settings) -> str:
    key = f"{engine}|{settings.voice}|{settings.tts_rate}|{settings.tts_pitch}|{text}"
    return f"{engine}_{hashlib.sha256(key.encode()).hexdigest()[:16]}.wav"


class TTSStage:
    name = "tts"

    def __init__(
        self,
        engines: Mapping[str, Synth] | None = None,
        fallback: Synth = say_synth,
        retry_delay_sec: float = RETRY_DELAY_SEC,
        speech_rate: SpeechRate = settings_words_per_sec,
    ) -> None:
        self._engines = dict(engines) if engines is not None else default_engines()
        self._fallback = fallback
        self._speech_rate = speech_rate
        self._retry_delay = retry_delay_sec

    def outputs(self, project: Project) -> list[str]:
        return [MANIFEST]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        s = project.settings
        return "|".join([config.file_signature(project_dir / SCRIPT), s.tts_engine, s.voice, s.tts_rate, s.tts_pitch])

    def run(self, ctx: StageContext) -> Project:
        try:
            script, items = load_script(ctx.path(SCRIPT))
        except ValueError as exc:
            raise StageFailure("script_invalid", str(exc)) from exc
        self._check_settings(ctx.project.settings)
        self._warn_speed_mismatch(ctx, script.words_per_sec)
        lines = [i for i in items if i["type"] == "narrate"]
        tts_dir = ctx.path(TTS_DIR)
        tts_dir.mkdir(parents=True, exist_ok=True)

        manifest: list[dict[str, Any]] = []
        created = 0
        try:
            for n, line in enumerate(lines, start=1):
                entry, is_new = self._line(ctx, tts_dir, line["id"], line["text"])
                manifest.append(entry)
                created += is_new
                ctx.emitter.progress(self.name, n, len(lines), line["id"])
        finally:
            self._close_engines()  # the VieNeu model holds ~1 GB of RAM

        config.write_json_atomic(ctx.path(MANIFEST), manifest)
        self._remove_unused(tts_dir, {Path(e["file"]).name for e in manifest})
        total = sum(e["duration"] for e in manifest)
        ctx.emitter.log("info", f"{len(lines)} câu, tạo mới {created}, tổng lời kể {total:.1f}s")
        ctx.emitter.artifact(self.name, MANIFEST)
        return ctx.project

    @staticmethod
    def _check_settings(settings: Settings) -> None:
        try:
            check_voice(settings.tts_engine, settings.voice)
        except TTSError as exc:
            raise StageFailure("voice_invalid", str(exc)) from exc
        try:
            rate_factor(settings.tts_rate)
        except TTSError as exc:
            raise StageFailure("settings_invalid", str(exc)) from exc

    def _warn_speed_mismatch(self, ctx: StageContext, script_speed: float | None) -> None:
        if not script_speed:
            return
        voice_speed = self._speech_rate(ctx.project.settings)
        if abs(voice_speed - script_speed) / script_speed <= SPEED_TOLERANCE:
            return
        change = script_speed / voice_speed - 1
        ctx.emitter.log(
            "warning",
            f"Kịch bản viết cho giọng đọc {format_rate(script_speed)} từ/giây, giọng hiện tại đọc "
            f"{format_rate(voice_speed)} từ/giây: lời kể sẽ {'ngắn' if change < 0 else 'dài'} hơn khoảng "
            f"{abs(change):.0%}. Chạy lại stage script để viết lại cho vừa thời lượng.",
        )

    def _close_engines(self) -> None:
        for engine in self._engines.values():
            close = getattr(engine, "close", None)
            if close is not None:
                close()

    def _line(self, ctx: StageContext, tts_dir: Path, line_id: str, text: str) -> tuple[dict[str, Any], bool]:
        settings = ctx.project.settings
        engine = settings.tts_engine
        out = tts_dir / _file_name(engine, text, settings)
        if out.is_file():
            return self._entry(line_id, out, engine), False
        attempts = EDGE_ATTEMPTS if engine == "edge" else 1
        error = self._try_engine(self._engines[engine], attempts, text, settings, out)
        if error is None:
            return self._entry(line_id, out, engine), True
        if engine != "edge":
            raise StageFailure("tts_failed", f"{line_id}: không tạo được giọng đọc ({error})", True) from error

        ctx.emitter.log("warning", f"{line_id}: edge-tts lỗi ({error}), dùng giọng dự phòng say -v Linh")
        say_file = tts_dir / _file_name("say", text, settings)
        if say_file.is_file():
            return self._entry(line_id, say_file, "say"), False
        try:
            self._synth(self._fallback, text, settings, say_file)
        except TTSError as exc:
            raise StageFailure("tts_failed", f"{line_id}: không tạo được giọng đọc ({error}; {exc})", True) from exc
        return self._entry(line_id, say_file, "say"), True

    def _try_engine(self, synth: Synth, attempts: int, text: str, settings: Settings, out: Path) -> TTSError | None:
        error: TTSError | None = None
        for attempt in range(attempts):
            try:
                self._synth(synth, text, settings, out)
                return None
            except TTSError as exc:
                error = exc
                if attempt < attempts - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
        return error

    @staticmethod
    def _synth(engine: Synth, text: str, settings: Settings, out: Path) -> None:
        tmp = out.with_name(f"{out.stem}.tmp.wav")
        try:
            engine(text, settings.voice, settings.tts_rate, settings.tts_pitch, tmp)
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _entry(line_id: str, path: Path, engine: str) -> dict[str, Any]:
        return {"id": line_id, "file": f"tts/{path.name}", "duration": round(wav_duration(path), 3), "engine": engine}

    @staticmethod
    def _remove_unused(tts_dir: Path, keep: set[str]) -> None:
        for wav in tts_dir.glob("*.wav"):
            if wav.name not in keep:
                wav.unlink()
