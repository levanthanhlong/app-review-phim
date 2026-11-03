import pytest

from recap.beats import (
    BeatPlan,
    dialogue_between,
    in_speech,
    plan_beats,
    speech_intervals,
    split_long_shot,
)
from recap.models import TranscriptSegment, Word

MIN, MAX = 4.0, 20.0


def seg(start: float, end: float, text: str, words: list[tuple[float, float, str]] | None = None) -> TranscriptSegment:
    return TranscriptSegment(
        id=0, start=start, end=end, text=text, words=[Word(start=s, end=e, word=w) for s, e, w in words or []]
    )


def durations(plans: list[BeatPlan]) -> list[float]:
    return [round(p.end - p.start, 3) for p in plans]


# ---- speech intervals -----------------------------------------------------

def test_speech_intervals_prefer_words_and_merge_small_gaps() -> None:
    segs = [seg(0, 5, "a b c", [(0.0, 0.5, "a"), (0.6, 1.0, "b"), (3.0, 3.5, "c")])]
    assert speech_intervals(segs) == [(0.0, 1.0), (3.0, 3.5)]


def test_speech_intervals_fall_back_to_segments() -> None:
    assert speech_intervals([seg(1, 2, "x"), seg(5, 6, "y")]) == [(1.0, 2.0), (5.0, 6.0)]


def test_in_speech() -> None:
    speech = [(1.0, 2.0)]
    assert in_speech(1.5, speech)
    assert not in_speech(0.5, speech)
    assert not in_speech(2.5, speech)


# ---- splitting long shots ---------------------------------------------------

def test_short_shot_is_not_split() -> None:
    assert split_long_shot((0.0, 15.0), [], MIN, MAX) == [(0.0, 15.0)]


def test_long_shot_split_evenly_without_speech() -> None:
    pieces = split_long_shot((0.0, 45.0), [], MIN, MAX)
    assert pieces == [(0.0, 15.0), (15.0, 30.0), (30.0, 45.0)]


def test_long_shot_split_avoids_speech() -> None:
    # Ideal split point 12.5 is inside speech (10-14); expect a cut in silence instead.
    pieces = split_long_shot((0.0, 25.0), [(10.0, 14.0)], MIN, MAX)
    assert len(pieces) == 2
    cut = pieces[0][1]
    assert not in_speech(cut, [(10.0, 14.0)])
    assert all(MIN <= e - s <= MAX for s, e in pieces)


def test_split_pieces_are_contiguous() -> None:
    pieces = split_long_shot((3.0, 100.0), [(20.0, 30.0), (50.0, 51.0)], MIN, MAX)
    assert pieces[0][0] == 3.0 and pieces[-1][1] == 100.0
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))
    assert all(e - s <= MAX for s, e in pieces)


# ---- merging shots into beats -------------------------------------------------

def test_short_shots_merge_until_min() -> None:
    shots = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.5), (4.5, 6.0), (6.0, 10.0)]
    plans = plan_beats(shots, [], MIN, MAX)
    assert durations(plans) == [4.5, 5.5]
    assert plans[0].shots == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.5)]


def test_beats_cover_whole_range_contiguously() -> None:
    shots = [(float(i), float(i + 1)) for i in range(60)]
    plans = plan_beats(shots, [], MIN, MAX)
    assert plans[0].start == 0.0 and plans[-1].end == 60.0
    assert all(a.end == b.start for a, b in zip(plans, plans[1:]))
    assert all(MIN <= p.end - p.start <= MAX for p in plans)


def test_cut_is_delayed_while_someone_is_talking() -> None:
    # Speech 3.5-7.5 covers boundaries at 4, 5, 6, 7 -> first silent boundary is 8.
    shots = [(float(i), float(i + 1)) for i in range(12)]
    plans = plan_beats(shots, [(3.5, 7.5)], MIN, MAX)
    assert plans[0].end == 8.0


def test_speech_never_pushes_beat_over_max() -> None:
    shots = [(float(i), float(i + 1)) for i in range(40)]
    plans = plan_beats(shots, [(0.0, 40.0)], MIN, MAX)
    assert all(p.end - p.start <= MAX for p in plans)


def test_long_single_shot_becomes_several_beats() -> None:
    plans = plan_beats([(0.0, 90.0)], [], MIN, MAX)
    assert len(plans) >= 5
    assert all(p.end - p.start <= MAX for p in plans)


def test_short_tail_is_merged_into_previous_beat() -> None:
    plans = plan_beats([(0.0, 10.0), (10.0, 11.0)], [], MIN, MAX)
    assert durations(plans) == [11.0]


def test_short_tail_kept_if_merging_would_exceed_max() -> None:
    plans = plan_beats([(0.0, 19.0), (19.0, 21.0)], [], MIN, MAX)
    assert durations(plans) == [19.0, 2.0]


def test_empty_shots() -> None:
    assert plan_beats([], [], MIN, MAX) == []


# ---- dialogue --------------------------------------------------------------

def test_dialogue_uses_word_midpoints() -> None:
    segs = [seg(0, 6, "xin chào bạn", [(0.0, 1.0, " xin"), (1.0, 2.0, " chào"), (5.0, 6.0, " bạn")])]
    assert dialogue_between(segs, 0.0, 4.0) == "xin chào"
    assert dialogue_between(segs, 4.0, 8.0) == "bạn"


def test_dialogue_falls_back_to_segment_midpoint() -> None:
    segs = [seg(0, 2, " Câu một."), seg(3, 5, " Câu hai.")]
    assert dialogue_between(segs, 0.0, 2.5) == "Câu một."
    assert dialogue_between(segs, 2.5, 9.0) == "Câu hai."


@pytest.mark.parametrize("bad", [(0.0, 4.0, 3.0), (0.0, 0.0, 20.0)])
def test_invalid_limits_rejected(bad: tuple[float, float, float]) -> None:
    _, mn, mx = bad
    with pytest.raises(ValueError):
        plan_beats([(0.0, 10.0)], [], mn, mx)
