"""ffprobe / ffmpeg helpers."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Any

from recap.models import AudioTrack, MediaInfo

PROGRESS_INTERVAL_SEC = 0.5
TERMINATE_TIMEOUT_SEC = 5


class MediaError(Exception):
    pass


def parse_rate(rate: str) -> float:
    num, _, den = rate.partition("/")
    try:
        n, d = float(num), float(den or 1)
    except ValueError:
        return 0.0
    return n / d if d else 0.0


def parse_out_time(line: str) -> float | None:
    """Parse an `out_time_us=...` line from `ffmpeg -progress`. Returns seconds or None."""
    key, _, value = line.strip().partition("=")
    if key != "out_time_us":
        return None
    try:
        return int(value) / 1_000_000
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def probe(path: Path) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"ffprobe không đọc được {path.name}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover_art(s)), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise MediaError(f"Không xác định được thời lượng của {path.name}")
    return MediaInfo(
        duration=duration,
        width=_to_int(video.get("width")) if video else None,
        height=_to_int(video.get("height")) if video else None,
        fps=parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0") if video else None,
        audio_tracks=[_audio_track(i, s) for i, s in enumerate(audio)],
    )


def _is_cover_art(stream: dict[str, Any]) -> bool:
    return bool(stream.get("disposition", {}).get("attached_pic"))


def _audio_track(index: int, stream: dict[str, Any]) -> AudioTrack:
    return AudioTrack(
        index=index,
        codec=stream.get("codec_name"),
        language=stream.get("tags", {}).get("language"),
        channels=_to_int(stream.get("channels")),
        sample_rate=_to_int(stream.get("sample_rate")),
        is_default=bool(stream.get("disposition", {}).get("default")),
    )


def extract_frame(video: Path, at_sec: float, out: Path, width: int) -> None:
    """Save one JPEG frame at `at_sec`, scaled to `width` px wide."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
        "-ss", f"{at_sec:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise MediaError(f"Không lấy được frame tại {at_sec:.2f}s: {proc.stderr.strip()}")


def probe_image_width(path: Path) -> int:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0"]
    proc = subprocess.run([*cmd, str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"ffprobe không đọc được {path.name}: {proc.stderr.strip()}")
    return int(proc.stdout.strip())


def run_ffmpeg(
    args: Sequence[str],
    total_sec: float,
    on_progress: Callable[[float], None],
    log: IO[str],
) -> None:
    """Run ffmpeg, reporting progress (seconds of output written). Raw stderr goes to `log`.

    If interrupted (e.g. SIGTERM raises in the main thread), ffmpeg is terminated before re-raising.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        _pump_progress(proc, total_sec, on_progress)
        code = proc.wait()
    except BaseException:
        _terminate(proc)
        raise
    if code != 0:
        raise MediaError(f"ffmpeg lỗi (exit {code}), xem log để biết chi tiết")


def _pump_progress(proc: subprocess.Popen[str], total_sec: float, on_progress: Callable[[float], None]) -> None:
    assert proc.stdout is not None
    last_report = 0.0
    for line in proc.stdout:
        seconds = parse_out_time(line)
        now = time.monotonic()
        if seconds is None or now - last_report < PROGRESS_INTERVAL_SEC:
            continue
        last_report = now
        on_progress(min(seconds, total_sec))


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=TERMINATE_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
