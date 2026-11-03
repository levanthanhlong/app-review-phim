import io
import json
import subprocess
import sys
from pathlib import Path

from recap import events

WORKER_DIR = Path(__file__).resolve().parents[1]


def _lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_each_event_is_one_json_line() -> None:
    # Arrange
    buf = io.StringIO()
    em = events.Emitter(stream=buf)

    # Act
    em.stage_start("describe", total=742)
    em.progress("describe", done=120, total=742, message="B0120")
    em.artifact("scenes", "work/scenes.json")
    em.stage_end("describe", status="done", elapsed_sec=1834.0)
    em.error("script", code="llm_quota", message="hết hạn mức", retryable=True)
    em.result("doctor", {"ok": True})

    # Assert
    assert _lines(buf) == [
        {"type": "stage_start", "stage": "describe", "total": 742},
        {"type": "progress", "stage": "describe", "done": 120, "total": 742, "message": "B0120"},
        {"type": "artifact", "stage": "scenes", "path": "work/scenes.json"},
        {"type": "stage_end", "stage": "describe", "status": "done", "elapsed_sec": 1834.0, "skipped": False},
        {"type": "error", "stage": "script", "code": "llm_quota", "message": "hết hạn mức", "retryable": True},
        {"type": "result", "command": "doctor", "data": {"ok": True}},
    ]


def test_protocol_stdout_is_not_polluted_by_library_prints() -> None:
    # Arrange: a library that prints to stdout from Python and from a child process (C-level fd 1)
    code = (
        "import os, sys\n"
        "from recap import events\n"
        "em = events.Emitter(stream=events.take_protocol_stdout())\n"
        "print('Detected language: Vietnamese')\n"
        "os.system('echo from-child')\n"
        "em.log('info', 'ok')\n"
    )

    # Act
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=WORKER_DIR)

    # Assert
    assert proc.returncode == 0, proc.stderr
    assert [json.loads(line) for line in proc.stdout.splitlines()] == [{"type": "log", "level": "info", "message": "ok"}]
    assert "Detected language" in proc.stderr and "from-child" in proc.stderr


def test_unicode_is_not_escaped() -> None:
    buf = io.StringIO()
    events.Emitter(stream=buf).log("info", "Phim hay")
    assert "Phim hay" in buf.getvalue()


def test_log_goes_to_stdout_stderr_and_file(tmp_path) -> None:
    # Arrange
    out, err = io.StringIO(), io.StringIO()
    log_file = tmp_path / "ingest.log"
    em = events.Emitter(stream=out, raw_stream=err)

    # Act
    with em.stage_log(log_file):
        em.log("warning", "track 2 missing")
        em.raw("ffmpeg raw line")

    # Assert
    assert _lines(out) == [{"type": "log", "level": "warning", "message": "track 2 missing"}]
    assert "track 2 missing" in err.getvalue()
    assert "ffmpeg raw line" in err.getvalue()
    content = log_file.read_text()
    assert "track 2 missing" in content and "ffmpeg raw line" in content
