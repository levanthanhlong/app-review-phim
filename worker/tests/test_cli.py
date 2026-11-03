"""End-to-end tests: run `python -m recap ...` as a subprocess, like the Swift app does."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from recap import config, media
from recap.events import Emitter
from recap.models import StageStatus
from recap.runner import run_stage

WORKER_DIR = Path(__file__).resolve().parents[1]


def run_cli(*args: str, env: dict[str, str] | None = None) -> tuple[int, list[dict]]:
    proc = subprocess.run(
        [sys.executable, "-m", "recap", *args],
        cwd=WORKER_DIR,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=300,
    )
    return proc.returncode, [json.loads(line) for line in proc.stdout.splitlines()]


def new_project(root: Path, clip: Path, title: str = "Clip") -> Path:
    code, evs = run_cli("new", "--root", str(root), "--title", title, "--source", str(clip))
    assert code == 0, evs
    return Path(evs[-1]["data"]["project"])


@pytest.mark.integration
def test_new_prints_result_with_project_path(tmp_path: Path, clip_5s: Path) -> None:
    project_dir = new_project(tmp_path, clip_5s)
    assert (project_dir / "project.json").exists()


@pytest.mark.integration
def test_ingest_creates_proxy_and_wav(tmp_path: Path, clip_5s: Path) -> None:
    # Arrange
    project_dir = new_project(tmp_path, clip_5s)

    # Act
    code, evs = run_cli("ingest", "--project", str(project_dir))

    # Assert: events
    assert code == 0, evs
    types = [e["type"] for e in evs]
    assert types[0] == "stage_start" and types[-1] == "stage_end"
    assert evs[-1]["status"] == "done" and evs[-1]["skipped"] is False
    assert {e["path"] for e in evs if e["type"] == "artifact"} == {"work/proxy_480p.mp4", "work/audio_16k.wav"}
    # Assert: files
    proxy = media.probe(project_dir / "work/proxy_480p.mp4")
    assert proxy.height == 480 and proxy.fps == pytest.approx(25.0)
    wav = media.probe(project_dir / "work/audio_16k.wav")
    assert wav.audio_tracks[0].sample_rate == 16000 and wav.audio_tracks[0].channels == 1
    assert not list((project_dir / "work").glob("*.tmp.*"))
    assert (project_dir / "logs/ingest.log").exists()
    # Assert: project.json
    project = config.load_project(project_dir)
    assert project.stages["ingest"].status == StageStatus.DONE
    assert project.stages["ingest"].elapsed_sec is not None
    assert project.media is not None and project.media.duration == pytest.approx(5.0, abs=0.2)


@pytest.mark.integration
def test_ingest_second_run_is_skipped_unless_forced(tmp_path: Path, clip_5s: Path) -> None:
    project_dir = new_project(tmp_path, clip_5s)
    run_cli("ingest", "--project", str(project_dir))

    _, evs = run_cli("ingest", "--project", str(project_dir))
    assert evs[-1] == {**evs[-1], "type": "stage_end", "skipped": True}

    _, evs = run_cli("ingest", "--project", str(project_dir), "--force")
    assert evs[-1]["skipped"] is False


@pytest.mark.integration
def test_ingest_reruns_when_output_deleted(tmp_path: Path, clip_5s: Path) -> None:
    project_dir = new_project(tmp_path, clip_5s)
    run_cli("ingest", "--project", str(project_dir))
    (project_dir / "work/audio_16k.wav").unlink()

    _, evs = run_cli("ingest", "--project", str(project_dir))

    assert evs[-1]["skipped"] is False
    assert (project_dir / "work/audio_16k.wav").exists()


@pytest.mark.integration
def test_ingest_uses_default_audio_track(tmp_path: Path, clip_two_audio: Path) -> None:
    project_dir = new_project(tmp_path, clip_two_audio)
    code, _ = run_cli("ingest", "--project", str(project_dir))
    assert code == 0
    assert config.load_project(project_dir).stages["ingest"].fingerprint.endswith("|a1")


@pytest.mark.integration
def test_ingest_fails_without_audio(tmp_path: Path, clip_no_audio: Path) -> None:
    project_dir = new_project(tmp_path, clip_no_audio)

    code, evs = run_cli("ingest", "--project", str(project_dir))

    assert code != 0
    err = next(e for e in evs if e["type"] == "error")
    assert err["code"] == "no_audio" and err["retryable"] is False
    state = config.load_project(project_dir).stages["ingest"]
    assert state.status == StageStatus.FAILED and state.error is not None and state.error.code == "no_audio"


@pytest.mark.integration
def test_ingest_fails_when_source_removed(tmp_path: Path, clip_5s: Path) -> None:
    movie = tmp_path / "movie.mp4"
    movie.write_bytes(clip_5s.read_bytes())
    project_dir = new_project(tmp_path / "r", movie)
    movie.unlink()

    code, evs = run_cli("ingest", "--project", str(project_dir))

    assert code != 0
    assert next(e for e in evs if e["type"] == "error")["code"] == "source_missing"


@pytest.mark.integration
def test_sigterm_cancels_cleanly(tmp_path: Path, clip_long: Path) -> None:
    # Arrange
    project_dir = new_project(tmp_path, clip_long)
    proc = subprocess.Popen(
        [sys.executable, "-m", "recap", "ingest", "--project", str(project_dir)],
        cwd=WORKER_DIR, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    assert proc.stdout is not None

    # Act: wait for the first progress event, then cancel
    for line in proc.stdout:
        if json.loads(line)["type"] == "progress":
            proc.send_signal(signal.SIGTERM)
            break
    rest = [json.loads(line) for line in proc.stdout]
    proc.wait(timeout=30)

    # Assert
    assert proc.returncode != 0
    assert any(e["type"] == "error" and e["code"] == "cancelled" for e in rest)
    work = project_dir / "work"
    assert not list(work.glob("*.tmp.*"))
    assert not (work / "proxy_480p.mp4").exists() and not (work / "audio_16k.wav").exists()
    state = config.load_project(project_dir).stages["ingest"]
    assert state.status == StageStatus.FAILED and state.error is not None and state.error.code == "cancelled"


def test_unimplemented_stage_reports_error(tmp_path: Path) -> None:
    # Arrange: pretend every earlier stage is done so only "not implemented" can fail
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)
    project = config.load_project(project_dir)
    done = project.stages["ingest"].model_copy(update={"status": StageStatus.DONE})
    config.save_project(project_dir, project.model_copy(update={"stages": {n: done for n in project.stages}}))

    out = io.StringIO()

    # Act: a registry without "export" (every real stage is implemented now)
    ok = run_stage(project_dir, "export", {}, Emitter(stream=out, raw_stream=io.StringIO()))

    evs = [json.loads(line) for line in out.getvalue().splitlines()]
    assert not ok
    assert evs[-1]["type"] == "error" and evs[-1]["code"] == "not_implemented"


def test_run_requires_previous_stage_done(tmp_path: Path) -> None:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)

    code, evs = run_cli("transcribe", "--project", str(project_dir))

    assert code != 0
    assert evs[-1]["code"] == "dependency_not_done"


def test_doctor_prints_checks(tmp_path: Path) -> None:
    code, evs = run_cli("doctor", "--root", str(tmp_path))
    result = evs[-1]
    assert result["type"] == "result" and result["command"] == "doctor"
    names = {c["name"] for c in result["data"]["checks"]}
    assert {"python", "ffmpeg", "ffprobe", "claude", "ollama", "storage"} <= names
    assert code == (0 if result["data"]["ok"] else 1)


def test_voices_lists_catalog_with_default() -> None:
    code, evs = run_cli("voices")

    data = evs[-1]["data"]
    assert code == 0 and evs[-1]["command"] == "voices"
    assert data["default"] == {"engine": "vieneu", "voice": "Hải Đăng"}
    first = data["voices"][0]
    assert set(first) == {"engine", "id", "description", "gender", "region", "featured", "words_per_sec"}
    assert first["engine"] == "vieneu" and first["featured"] is True
    assert {v["engine"] for v in data["voices"]} == {"vieneu"}  # edge-tts is unreliable: hidden by default


def test_voices_can_include_edge() -> None:
    _, evs = run_cli("voices", "--include-edge")

    assert {v["engine"] for v in evs[-1]["data"]["voices"]} == {"vieneu", "edge"}


def test_voice_preview_rejects_unknown_voice(tmp_path: Path) -> None:
    out = tmp_path / "p.wav"

    code, evs = run_cli("voice-preview", "--voice", "Ai Đó", "--out", str(out))

    assert code == 1 and evs[-1]["type"] == "error" and evs[-1]["code"] == "voice_invalid"
    assert not out.exists()


@pytest.mark.integration
def test_voice_preview_makes_a_wav(tmp_path: Path) -> None:
    from recap.vieneu_tts import model_is_cached

    if not model_is_cached():
        pytest.skip("VieNeu model not downloaded")
    out = tmp_path / "p.wav"

    code, evs = run_cli("voice-preview", "--voice", "Ngọc Huyền", "--text", "Xin chào.", "--out", str(out))

    assert code == 0, evs
    data = evs[-1]["data"]
    assert data["file"] == str(out) and 0.3 < data["duration"] < 5 and out.is_file()
