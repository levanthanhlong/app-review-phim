from pathlib import Path

import pytest

from recap import media


@pytest.mark.integration
def test_probe_reads_basic_info(clip_5s: Path) -> None:
    info = media.probe(clip_5s)
    assert info.duration == pytest.approx(5.0, abs=0.2)
    assert (info.width, info.height) == (1280, 720)
    assert info.fps == pytest.approx(25.0)
    assert len(info.audio_tracks) == 1


@pytest.mark.integration
def test_probe_picks_default_audio_track(clip_two_audio: Path) -> None:
    info = media.probe(clip_two_audio)
    assert len(info.audio_tracks) == 2
    assert info.default_audio_track() == 1


@pytest.mark.integration
def test_probe_no_audio(clip_no_audio: Path) -> None:
    info = media.probe(clip_no_audio)
    assert info.audio_tracks == []
    assert info.default_audio_track() is None


def test_probe_rejects_non_media(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_text("not a video")
    with pytest.raises(media.MediaError):
        media.probe(bad)


def test_parse_progress_line() -> None:
    assert media.parse_out_time("out_time_us=2500000") == 2.5
    assert media.parse_out_time("out_time_us=N/A") is None
    assert media.parse_out_time("frame=12") is None


def test_parse_rate() -> None:
    assert media.parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert media.parse_rate("0/0") == 0.0
