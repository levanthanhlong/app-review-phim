"""Subtitle cues from the timeline, and SRT output."""

from __future__ import annotations

import pytest

from recap.fit import Segment, TimelineEntry
from recap.render import NARRATION_DELAY_SEC
from recap.subtitles import (
    MAX_CUE_CHARS,
    MAX_LINE_CHARS,
    Cue,
    build_cues,
    format_srt_time,
    split_cue_text,
    to_srt,
    wrap_lines,
)

LONG = (
    "Dọc đường, cóc rủ được cua càng, rồi gấu và cọp đang khát cháy họng cùng nhập đoàn đi kiện trời, "
    "khiến cả khu rừng xôn xao bàn tán suốt nhiều ngày liền."
)


def narrate(entry_id: str, start: float, tts_dur: float) -> TimelineEntry:
    return TimelineEntry(entry_id, "narrate", start, start + tts_dur + 0.25, [Segment(0, 1)], "tts/x.wav", tts_dur)


def test_short_text_is_one_cue() -> None:
    assert split_cue_text("Trời đang mưa.") == ["Trời đang mưa."]


def test_long_text_is_split_into_cues_that_fit_and_keep_every_word() -> None:
    chunks = split_cue_text(LONG)

    assert len(chunks) >= 2
    assert all(len(c) <= MAX_CUE_CHARS for c in chunks)
    assert " ".join(chunks).split() == LONG.split()


def test_split_prefers_punctuation() -> None:
    chunks = split_cue_text(LONG)

    assert chunks[0].endswith(",")


def test_single_word_longer_than_a_cue_is_kept() -> None:
    word = "a" * (MAX_CUE_CHARS + 5)

    assert split_cue_text(f"{word} b") == [word, "b"]


def test_wrap_keeps_short_text_on_one_line() -> None:
    assert wrap_lines("Trời đang mưa.") == "Trời đang mưa."


def test_wrap_splits_long_text_near_the_middle() -> None:
    text = "Nắng lửa thiêu cháy cây cối, sông cạn nước, cá mắc cạn, muôn loài khát."

    lines = wrap_lines(text).split("\n")

    assert len(lines) == 2 and " ".join(lines) == text
    assert all(len(line) <= MAX_LINE_CHARS for line in lines)
    assert abs(len(lines[0]) - len(lines[1])) < 15


def test_cues_follow_the_narration_audio() -> None:
    timeline = [
        narrate("L0001", 0.0, 2.0),
        TimelineEntry("L0002", "original", 2.25, 6.25, [Segment(10, 14)]),
        narrate("L0003", 6.25, 3.0),
    ]
    texts = {"L0001": "Trời đang mưa.", "L0003": "Cóc lên trời kiện."}

    cues = build_cues(timeline, texts)

    assert [c.text for c in cues] == ["Trời đang mưa.", "Cóc lên trời kiện."]
    assert cues[0].start == pytest.approx(NARRATION_DELAY_SEC)
    assert cues[0].end == pytest.approx(NARRATION_DELAY_SEC + 2.0)
    assert cues[1].start == pytest.approx(6.25 + NARRATION_DELAY_SEC)


def test_long_line_time_is_shared_by_characters() -> None:
    cues = build_cues([narrate("L0001", 10.0, 8.0)], {"L0001": LONG})

    assert len(cues) >= 2
    assert cues[0].start == pytest.approx(10.0 + NARRATION_DELAY_SEC)
    assert cues[-1].end == pytest.approx(10.0 + NARRATION_DELAY_SEC + 8.0)
    for a, b in zip(cues, cues[1:]):
        assert a.end == pytest.approx(b.start)
    total_chars = sum(len(c.text.replace("\n", " ")) for c in cues)
    first_share = len(cues[0].text.replace("\n", " ")) / total_chars
    assert (cues[0].end - cues[0].start) / 8.0 == pytest.approx(first_share)


def test_line_without_text_is_skipped() -> None:
    assert build_cues([narrate("L0001", 0.0, 2.0)], {}) == []


@pytest.mark.parametrize(("sec", "text"), [(0, "00:00:00,000"), (3725.4567, "01:02:05,457"), (59.9996, "00:01:00,000")])
def test_srt_time(sec: float, text: str) -> None:
    assert format_srt_time(sec) == text


def test_srt_format() -> None:
    cues = [Cue(0.1, 2.1, "Trời đang mưa."), Cue(2.5, 4.0, "Dòng một\nDòng hai")]

    assert to_srt(cues) == (
        "1\n00:00:00,100 --> 00:00:02,100\nTrời đang mưa.\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\nDòng một\nDòng hai\n"
    )


def test_short_gaps_between_lines_are_closed_to_avoid_flicker() -> None:
    timeline = [narrate("L0001", 0.0, 2.0), narrate("L0002", 2.25, 2.0), narrate("L0003", 10.0, 1.0)]
    texts = {"L0001": "Một.", "L0002": "Hai.", "L0003": "Ba."}

    cues = build_cues(timeline, texts)

    assert cues[0].end == pytest.approx(cues[1].start)  # 0.35 s gap closed
    assert cues[1].end == pytest.approx(2.25 + NARRATION_DELAY_SEC + 2.0)  # long gap before L0003 kept
