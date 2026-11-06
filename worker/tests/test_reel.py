import subprocess
from pathlib import Path

import pytest

from recap import media
from recap.fit import TimelineEntry
from recap.reel import candidate_cuts, rank_cuts, render_args
from tests.conftest import HAS_FFMPEG, make_clip


def timeline(count: int) -> list[TimelineEntry]:
    return [TimelineEntry(f"L{i:04}", "narrate", (i - 1) * 10, i * 10, []) for i in range(1, count + 1)]


def test_candidate_boundaries_reserve_time_for_cta_and_leave_story_for_full_video() -> None:
    assert [entry.out_end for entry in candidate_cuts(timeline(32))] == [240, 250, 260, 270, 280]
    assert [entry.out_end for entry in candidate_cuts(timeline(25))] == [240]
    assert [entry.out_end for entry in candidate_cuts(timeline(5))] == [10, 20, 30, 40]
    assert candidate_cuts(timeline(1)) == []


def test_model_can_only_pick_real_boundaries_and_cta_keeps_the_comment_link() -> None:
    class Provider:
        def generate(self, prompt: str, material: str) -> str:
            assert "L0026" in material and "manh mối" in material
            return '''```json
            [{"id":"made-up","reason":"Sai","lead":"Sai"},
             {"id":"L0026","reason":"Manh mối mới","lead":"Vậy ai đã để lại lá thư?"}]
            ```'''

    entries = timeline(32)
    result = rank_cuts(entries, candidate_cuts(entries), {"L0026": "Cô phát hiện manh mối."}, "Mắm", Provider())

    assert list(result) == ["L0026"]
    assert result["L0026"][1].startswith("Vậy ai đã để lại lá thư?")
    assert "bình luận" in result["L0026"][1]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not available")
def test_render_joins_opening_and_spoken_cta_in_vertical_mp4(tmp_path: Path) -> None:
    clip = make_clip(tmp_path / "source.mp4", 4)
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=1", str(voice)], check=True)
    out = tmp_path / "reel.mp4"

    subprocess.run(["ffmpeg", "-v", "error", *render_args(clip, voice, 1, 3, 1, out)], check=True)

    info = media.probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(3, abs=0.1)
    assert info.audio_tracks
