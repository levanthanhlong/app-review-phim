"""Background music: chosen per project, mixed under the whole recap at the final join."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from recap import config, media
from recap.fit import Segment, TimelineEntry
from recap.models import Settings
from recap.music import (
    CLIP_FACTOR,
    MusicBed,
    MusicCue,
    MusicError,
    check_music_file,
    clip_intervals,
    loop_plan,
    music_filter,
    music_inputs,
    music_signature,
    narration_intervals,
)
from tests.test_assemble import clip_30s, logs, prepared_project  # noqa: F401  (fixture re-export)
from tests.test_cli import run_cli

MUSIC_HZ = 1500


def entry(id_: str, type_: str, start: float, end: float) -> TimelineEntry:
    return TimelineEntry(id_, type_, start, end, [Segment(0.0, end - start)])  # type: ignore[arg-type]


def make_music(path: Path, seconds: float, freq: int = MUSIC_HZ) -> Path:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "44100", "-ac", "2", str(path)], check=True)  # fmt: skip
    return path


# --- pure helpers ------------------------------------------------------------------------


def test_clip_intervals_merge_adjacent_original_lines_and_skip_narration() -> None:
    timeline = [
        entry("L1", "narrate", 0.0, 6.0),
        entry("L2", "original", 6.0, 10.0),
        entry("L3", "original", 10.0, 12.5),
        entry("L4", "narrate", 12.5, 15.0),
        entry("L5", "original", 15.0, 18.0),
    ]

    assert clip_intervals(timeline) == ((6.0, 12.5), (15.0, 18.0))


@pytest.mark.parametrize(
    ("track", "total", "copies"),
    [(120.0, 90.0, 1), (60.0, 150.0, 3), (60.0, 60.0, 1), (1.0, 600.0, 0)],
)
def test_loop_plan_counts_crossfaded_copies(track: float, total: float, copies: int) -> None:
    planned, xfade = loop_plan(track, total)

    assert planned == copies
    if copies > 1:  # the copies minus their overlaps must cover the recap
        assert copies * track - (copies - 1) * xfade >= total


INF = float("inf")


def bed(*cues: MusicCue, bed_volume: float = 0.25, voice_volume: float = 0.12) -> MusicBed:
    return MusicBed(tuple(cues), bed_volume, voice_volume)


def test_narration_intervals_merge_back_to_back_lines() -> None:
    timeline = [entry("L1", "narrate", 0.0, 6.0), entry("L2", "narrate", 6.0, 9.0), entry("L3", "original", 9.0, 12.0),
                entry("L4", "narrate", 12.0, 15.0)]  # fmt: skip

    assert narration_intervals(timeline) == ((0.0, 9.0), (12.0, 15.0))


def test_library_track_loops_over_the_recap_and_ducks_under_voice_and_clips() -> None:
    music = bed(MusicCue(Path("m.mp3"), 60.0, 0.0, INF))
    timeline = [entry("L1", "narrate", 0.0, 50.0), entry("L2", "original", 50.0, 60.0), entry("L3", "narrate", 60.0, 150.0)]

    graph = music_filter(music, timeline, first_input=1)

    assert graph.count("acrossfade") == 2  # 3 copies cover 150 s
    assert "volume=0.25" in graph  # level with no narration
    assert "1-(1-0.48)" in graph  # 0.12 under narration = 0.48 of the bed level
    assert "enable='between(t,0,50)'" in graph and "enable='between(t,60,150)'" in graph
    assert f"1-(1-{0.12 * CLIP_FACTOR / 0.25:.6g})" in graph and "enable='between(t,50,60)'" in graph
    assert "afade=t=in" in graph and re.search(r"afade=t=out:st=147(\.0+)?:d=3", graph)
    assert "amix" not in graph.split("[bed]")[0]  # one cue: nothing to mix
    assert graph.endswith("[mus]")


def test_ai_cues_play_in_turn_and_crossfade_at_the_boundary() -> None:
    music = bed(MusicCue(Path("a.wav"), 70.0, 0.0, 60.0), MusicCue(Path("b.wav"), 95.0, 60.0, 150.0))
    timeline = [entry("L1", "narrate", 0.0, 60.0), entry("L2", "narrate", 60.0, 150.0)]

    graph = music_filter(music, timeline, first_input=1)

    # cue 1 runs 1 s past the boundary and fades out; cue 2 starts 1 s early and fades in
    assert re.search(r"\[1:a\]atrim=duration=61,.*afade=t=out:st=59:d=2,adelay=0:all=1\[c0\]", graph)
    assert re.search(r"\[2:a\]atrim=duration=91,.*afade=t=in:d=2,adelay=59000:all=1\[c1\]", graph)
    assert "[c0][c1]amix=inputs=2:duration=longest:normalize=0" in graph
    assert "acrossfade" not in graph  # both tracks are long enough: no looping


def test_music_inputs_repeat_the_file_or_stream_loop_a_tiny_track() -> None:
    long_track = bed(MusicCue(Path("m.mp3"), 60.0, 0.0, INF))
    tiny = bed(MusicCue(Path("t.mp3"), 1.0, 0.0, INF))
    two = bed(MusicCue(Path("a.wav"), 70.0, 0.0, 60.0), MusicCue(Path("b.wav"), 50.0, 60.0, 150.0))

    assert music_inputs(long_track, total_sec=150.0) == ["-i", "m.mp3"] * 3
    assert music_inputs(tiny, total_sec=150.0) == ["-stream_loop", "-1", "-i", "t.mp3"]
    assert music_inputs(two, total_sec=150.0) == ["-i", "a.wav", "-i", "b.wav", "-i", "b.wav"]  # b loops once


def test_check_music_file_rejects_missing_and_non_audio(tmp_path: Path) -> None:
    text = tmp_path / "notes.txt"
    text.write_text("x")

    with pytest.raises(MusicError, match="Không tìm thấy"):
        check_music_file(str(tmp_path / "missing.mp3"))
    with pytest.raises(MusicError, match="định dạng"):
        check_music_file(str(text))


def test_signature_is_none_without_music(tmp_path: Path) -> None:
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")

    assert music_signature(Settings(), tmp_path) is None
    signed = music_signature(Settings(music_file=str(song), music_volume=0.1), tmp_path)
    assert signed is not None and "a.mp3" in signed and "0.1" in signed


def test_ai_signature_follows_the_plan_file(tmp_path: Path) -> None:
    plan = tmp_path / "work/music/plan.json"
    plan.parent.mkdir(parents=True)
    plan.write_text("{}")
    before = music_signature(Settings(music_source="ai"), tmp_path)

    plan.write_text('{"cues": []}')

    assert before is not None and "plan.json" in before
    assert music_signature(Settings(music_source="ai"), tmp_path) != before


# --- settings ------------------------------------------------------------------------------


def test_set_rejects_missing_music_and_out_of_range_volume(tmp_path: Path) -> None:
    movie = tmp_path / "m.mp4"
    movie.write_bytes(b"x")
    project_dir = config.create_project(tmp_path / "r", title="A", source=movie)

    missing = run_cli("set", "--project", str(project_dir), f"music_file={tmp_path / 'nope.mp3'}")
    loud = run_cli("set", "--project", str(project_dir), "music_volume=0.9")

    assert missing[0] == 1 and missing[1][-1]["code"] == "settings_invalid"
    assert loud[0] == 1 and loud[1][-1]["code"] == "settings_invalid"


# --- rendering ------------------------------------------------------------------------------


def band_rms_db(video: Path, start: float, seconds: float, freq: int = MUSIC_HZ) -> float:
    """RMS level (dB) of `video`'s audio around `freq`, in [start, start + seconds]."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(start), "-t", str(seconds), "-i", str(video), "-vn",
         "-af", f"bandpass=f={freq}:width_type=h:w=200,astats=measure_overall=RMS_level:measure_perchannel=none",
         "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    match = re.search(r"RMS level dB: (-?[\d.]+|-inf)", proc.stderr)
    assert match, proc.stderr[-500:]
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


@pytest.mark.integration
def test_assemble_mixes_music_and_ducks_it_under_original_clips(tmp_path: Path, clip_30s: Path) -> None:  # noqa: F811
    # Arrange: timeline = L0001 narrate 0–6 s, L0002 original 6–10 s, L0003 narrate 10–13 s
    project_dir = prepared_project(tmp_path, clip_30s)
    song = make_music(tmp_path / "nhạc nền.m4a", 5.0)  # shorter than the recap: must loop
    assert run_cli("assemble", "--project", str(project_dir))[0] == 0
    silent = project_dir / "output/recap_preview.mp4"
    before = band_rms_db(silent, 2.5, 2.0)

    # Act
    code, events = run_cli("set", "--project", str(project_dir), f"music_file={song}", "music_volume=0.3")
    assert code == 0, events
    assert "assemble" in events[-1]["data"]["stale"]
    code, events = run_cli("assemble", "--project", str(project_dir))

    # Assert
    assert code == 0, events
    assert "Render: 0 dòng mới, 3 dòng dùng cache" in logs(events)  # only the join is redone
    out = project_dir / "output/recap_preview.mp4"
    assert media.probe(out).duration == pytest.approx(13.0, abs=0.2)
    narration = band_rms_db(out, 2.5, 2.0)
    under_clip = band_rms_db(out, 7.2, 1.6)
    assert narration > before + 8  # the music is there
    assert narration > under_clip + 6  # and much quieter under the original clip


@pytest.mark.integration
def test_missing_music_file_fails_clearly(tmp_path: Path, clip_30s: Path) -> None:  # noqa: F811
    project_dir = prepared_project(tmp_path, clip_30s)
    song = make_music(tmp_path / "song.mp3", 3.0)
    assert run_cli("set", "--project", str(project_dir), f"music_file={song}")[0] == 0
    song.unlink()

    code, events = run_cli("assemble", "--project", str(project_dir))

    assert code != 0
    assert next(e for e in events if e["type"] == "error")["code"] == "music_missing"
