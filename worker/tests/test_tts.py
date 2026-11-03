"""TTS stage with fake engines (edge-tts needs the network; `say` is checked in an integration test)."""

from __future__ import annotations

import io
import json
import shutil
import wave
from pathlib import Path

import pytest

from recap import config
from recap.events import Emitter
from recap.models import StageStatus
from recap.runner import run_stage
from recap.stages.tts import TTSStage
from recap.tts_engines import TTSError, say_synth, wav_duration

SCRIPT = """title: A
target_duration_sec: 10
items:
- id: L0001
  type: narrate
  text: Một hai ba bốn năm sáu.
  clips:
  - {beat: B0001, start: "00:00:01", end: "00:00:03"}
- id: L0002
  type: original
  beat: B0001
  start: "00:00:04"
  end: "00:00:08"
- id: L0003
  type: narrate
  text: Bảy tám chín mười.
  clips:
  - {beat: B0001, start: "00:00:05", end: "00:00:07"}
"""


def write_wav(path: Path, seconds: float, rate: int = 44100) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))


class FakeEngine:
    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[str] = []
        self.fail_times = fail_times

    def __call__(self, text: str, voice: str, rate: str, pitch: str, out: Path) -> None:
        self.calls.append(text)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise TTSError("network down")
        write_wav(out, 0.1 * len(text.split()))


EDGE = {"tts_engine": "edge", "voice": "vi-VN-NamMinhNeural"}


def make_project(tmp_path: Path, script: str = SCRIPT, **settings: str) -> Path:
    """Project with script done. Settings default to edge-tts (most tests here exercise its fallback)."""
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)
    (project_dir / "script.yaml").write_text(script)
    project = config.load_project(project_dir)
    done = project.stages["script"].model_copy(update={"status": StageStatus.DONE})
    before = ("ingest", "transcribe", "scenes", "describe", "script")
    stages = {n: (done if n in before else s) for n, s in project.stages.items()}
    new_settings = project.settings.model_copy(update=settings or EDGE)
    config.save_project(project_dir, project.model_copy(update={"stages": stages, "settings": new_settings}))
    return project_dir


def set_settings(project_dir: Path, **settings: str) -> None:
    project = config.load_project(project_dir)
    new_settings = project.settings.model_copy(update=settings)
    config.save_project(project_dir, project.model_copy(update={"settings": new_settings}))


def run(
    project_dir: Path,
    edge: FakeEngine,
    fallback: FakeEngine,
    vieneu: FakeEngine | None = None,
    force: bool = False,
    words_per_sec: float = 3.0,
) -> tuple[bool, list[dict]]:
    out = io.StringIO()
    engines = {"edge": edge, "vieneu": vieneu or FakeEngine()}
    stage = TTSStage(engines=engines, fallback=fallback, retry_delay_sec=0, speech_rate=lambda s: words_per_sec)
    ok = run_stage(project_dir, "tts", {"tts": stage}, Emitter(stream=out, raw_stream=io.StringIO()), force=force)
    return ok, [json.loads(line) for line in out.getvalue().splitlines()]


def manifest(project_dir: Path) -> list[dict]:
    return json.loads((project_dir / "work/tts/manifest.json").read_text())


def test_one_wav_per_narration_with_duration(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    edge = FakeEngine()

    ok, _ = run(project_dir, edge, FakeEngine())

    assert ok and edge.calls == ["Một hai ba bốn năm sáu.", "Bảy tám chín mười."]
    entries = manifest(project_dir)
    assert [e["id"] for e in entries] == ["L0001", "L0003"]
    assert entries[0]["duration"] == pytest.approx(0.6) and entries[0]["engine"] == "edge"
    assert (project_dir / "work" / entries[0]["file"]).is_file()


def test_editing_one_line_regenerates_only_that_line(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeEngine(), FakeEngine())
    (project_dir / "script.yaml").write_text(SCRIPT.replace("Bảy tám chín mười.", "Câu mới hoàn toàn khác."))

    edge = FakeEngine()
    ok, _ = run(project_dir, edge, FakeEngine())

    assert ok and edge.calls == ["Câu mới hoàn toàn khác."]


def test_second_run_is_skipped(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, FakeEngine(), FakeEngine())

    edge = FakeEngine()
    _, events = run(project_dir, edge, FakeEngine())

    assert events[-1]["skipped"] is True and edge.calls == []


def test_falls_back_to_say_after_three_edge_failures_and_warns(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path, SCRIPT.split("- id: L0002")[0])
    edge, fallback = FakeEngine(fail_times=3), FakeEngine()

    ok, events = run(project_dir, edge, fallback)

    assert ok and len(edge.calls) == 3 and len(fallback.calls) == 1
    assert manifest(project_dir)[0]["engine"] == "say"
    assert any(e["type"] == "log" and e["level"] == "warning" and "say" in e["message"] for e in events)


def test_next_run_retries_edge_for_fallback_lines(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path, SCRIPT.split("- id: L0002")[0])
    run(project_dir, FakeEngine(fail_times=3), FakeEngine())

    edge = FakeEngine()
    run(project_dir, edge, FakeEngine(), force=True)

    assert len(edge.calls) == 1 and manifest(project_dir)[0]["engine"] == "edge"


def test_fails_when_both_engines_fail(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)

    ok, events = run(project_dir, FakeEngine(fail_times=99), FakeEngine(fail_times=99))

    assert not ok
    err = next(e for e in events if e["type"] == "error")
    assert err["code"] == "tts_failed" and err["retryable"] is True


def test_invalid_script_yaml_is_reported(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path, "items: [ {type: narrate} ]")

    ok, events = run(project_dir, FakeEngine(), FakeEngine())

    assert not ok and next(e for e in events if e["type"] == "error")["code"] == "script_invalid"


class ClosableEngine(FakeEngine):
    def __init__(self, fail_times: int = 0) -> None:
        super().__init__(fail_times)
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def vieneu_project(tmp_path: Path, script: str = SCRIPT) -> Path:
    return make_project(tmp_path, script, tts_engine="vieneu", voice="Hải Đăng")


def test_vieneu_engine_makes_every_line_and_is_closed(tmp_path: Path) -> None:
    project_dir = vieneu_project(tmp_path)
    vieneu, edge = ClosableEngine(), FakeEngine()

    ok, _ = run(project_dir, edge, FakeEngine(), vieneu)

    assert ok and len(vieneu.calls) == 2 and edge.calls == []
    assert {e["engine"] for e in manifest(project_dir)} == {"vieneu"}
    assert all(e["file"].startswith("tts/vieneu_") for e in manifest(project_dir))
    assert vieneu.closed == 1


def test_vieneu_failure_fails_without_retry_or_fallback(tmp_path: Path) -> None:
    project_dir = vieneu_project(tmp_path)
    vieneu, fallback = ClosableEngine(fail_times=99), FakeEngine()

    ok, events = run(project_dir, FakeEngine(), fallback, vieneu)

    assert not ok and len(vieneu.calls) == 1 and fallback.calls == []
    assert next(e for e in events if e["type"] == "error")["code"] == "tts_failed"
    assert vieneu.closed == 1  # model memory released even on failure


def test_unknown_vieneu_voice_fails_before_synthesis(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path, tts_engine="vieneu", voice="vi-VN-NamMinhNeural")
    vieneu = FakeEngine()

    ok, events = run(project_dir, FakeEngine(), FakeEngine(), vieneu)

    err = next(e for e in events if e["type"] == "error")
    assert not ok and err["code"] == "voice_invalid" and vieneu.calls == []
    assert "recap voices" in err["message"]


def test_invalid_rate_fails_before_synthesis(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path, tts_engine="vieneu", voice="Hải Đăng", tts_rate="nhanh")
    vieneu = FakeEngine()

    ok, events = run(project_dir, FakeEngine(), FakeEngine(), vieneu)

    err = next(e for e in events if e["type"] == "error")
    assert not ok and err["code"] == "settings_invalid" and vieneu.calls == []


def test_changing_voice_or_engine_regenerates(tmp_path: Path) -> None:
    project_dir = vieneu_project(tmp_path)
    run(project_dir, FakeEngine(), FakeEngine(), FakeEngine())

    set_settings(project_dir, voice="Đức Trí")
    vieneu = FakeEngine()
    _, events = run(project_dir, FakeEngine(), FakeEngine(), vieneu)
    assert events[-1]["skipped"] is False and len(vieneu.calls) == 2

    set_settings(project_dir, **EDGE)
    edge = FakeEngine()
    _, events = run(project_dir, edge, FakeEngine())
    assert events[-1]["skipped"] is False and len(edge.calls) == 2
    assert {e["engine"] for e in manifest(project_dir)} == {"edge"}


def warnings(events: list[dict]) -> list[str]:
    return [e["message"] for e in events if e["type"] == "log" and e["level"] == "warning"]


def test_warns_when_voice_speed_differs_from_script(tmp_path: Path) -> None:
    script = SCRIPT.replace("target_duration_sec: 10\n", "target_duration_sec: 10\nwords_per_sec: 3.0\n")
    project_dir = make_project(tmp_path, script)

    ok, events = run(project_dir, FakeEngine(), FakeEngine(), words_per_sec=4.5)

    assert ok
    assert any("3 từ/giây" in w and "4.5 từ/giây" in w and "script" in w for w in warnings(events))


def test_no_speed_warning_when_close_or_unknown(tmp_path: Path) -> None:
    script = SCRIPT.replace("target_duration_sec: 10\n", "target_duration_sec: 10\nwords_per_sec: 4.2\n")
    _, events = run(make_project(tmp_path, script), FakeEngine(), FakeEngine(), words_per_sec=4.5)
    assert warnings(events) == []

    (tmp_path / "b").mkdir()
    _, events = run(make_project(tmp_path / "b"), FakeEngine(), FakeEngine(), words_per_sec=4.5)  # no words_per_sec
    assert warnings(events) == []


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("say") is None or shutil.which("ffmpeg") is None, reason="needs macOS say + ffmpeg")
def test_say_engine_makes_44k_mono_wav(tmp_path: Path) -> None:
    out = tmp_path / "x.wav"
    say_synth("Xin chào các bạn", "vi-VN-NamMinhNeural", "+5%", "+0Hz", out)
    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getnchannels()) == (44100, 1)
    assert wav_duration(out) > 0.3
