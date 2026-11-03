"""Shared fixtures: synthetic clips generated with ffmpeg (no real movie needed)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_clip(
    path: Path,
    duration: float,
    size: str = "1280x720",
    audio_tracks: int = 1,
    default_audio: int = 0,
) -> Path:
    """Generate a test clip: moving test pattern + one sine tone per audio track."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={duration}"]
    for i in range(audio_tracks):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 220 * i}:sample_rate=48000:duration={duration}"]
    cmd += ["-map", "0:v"]
    for i in range(audio_tracks):
        cmd += ["-map", f"{i + 1}:a"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    for i in range(audio_tracks):
        cmd += [f"-disposition:a:{i}", "default" if i == default_audio else "0"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def clip_5s(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return make_clip(tmp_path_factory.mktemp("clips") / "clip 5s.mp4", 5)


@pytest.fixture(scope="session")
def clip_two_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return make_clip(tmp_path_factory.mktemp("clips") / "two_audio.mkv", 3, audio_tracks=2, default_audio=1)


@pytest.fixture(scope="session")
def clip_no_audio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return make_clip(tmp_path_factory.mktemp("clips") / "silent.mp4", 2, audio_tracks=0)


@pytest.fixture(scope="session")
def clip_long(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Long enough that ingest is still running when we send SIGTERM."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return make_clip(tmp_path_factory.mktemp("clips") / "long.mp4", 240, size="1920x1080")
