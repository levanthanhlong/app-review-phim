"""Alignment rules (spec 4.7) — pure functions, no I/O."""

import json

import pytest

from recap.fit import MIN_SPEED, PAD_SEC, ClipRef, Segment, build_timeline, fit_narration
from recap.models import Beat


def b(i: int, start: float, end: float) -> Beat:
    bid = f"B{i:04d}"
    return Beat(id=bid, start=start, end=end, shots=[(start, end)], keyframe=f"keyframes/{bid}.jpg")


BEATS = {x.id: x for x in [b(1, 0.0, 10.0), b(2, 10.0, 20.0), b(3, 20.0, 40.0)]}


def total(segments: list[Segment]) -> float:
    return sum((s.src_end - s.src_start) / s.speed for s in segments)


# ---- fit_narration ------------------------------------------------------------------

def test_exact_fit_keeps_clips() -> None:
    fitted = fit_narration([ClipRef("B0001", 2.0, 8.0)], BEATS, needed=6.0)
    assert fitted.segments == [Segment(2.0, 8.0, 1.0)] and fitted.fit == "ok"


def test_small_surplus_trims_tail_and_is_ok() -> None:
    fitted = fit_narration([ClipRef("B0001", 2.0, 8.5)], BEATS, needed=6.0)
    assert fitted.segments == [Segment(2.0, 8.0, 1.0)] and fitted.fit == "ok"


def test_large_surplus_drops_and_trims_later_clips() -> None:
    clips = [ClipRef("B0001", 0.0, 4.0), ClipRef("B0002", 10.0, 14.0), ClipRef("B0003", 20.0, 24.0)]
    fitted = fit_narration(clips, BEATS, needed=5.0)
    assert fitted.segments == [Segment(0.0, 4.0, 1.0), Segment(10.0, 11.0, 1.0)]
    assert fitted.fit == "trimmed"


def test_shortage_extends_last_clip_within_its_beat() -> None:
    fitted = fit_narration([ClipRef("B0001", 2.0, 5.0)], BEATS, needed=6.0)
    assert fitted.segments == [Segment(2.0, 8.0, 1.0)] and fitted.fit == "ok"


def test_extension_stops_at_beat_end_then_extends_first_clip_backwards() -> None:
    fitted = fit_narration([ClipRef("B0001", 6.0, 9.0)], BEATS, needed=6.0)
    assert fitted.segments == [Segment(4.0, 10.0, 1.0)] and fitted.fit == "ok"


def test_multi_clip_shortage_extends_last_then_first() -> None:
    clips = [ClipRef("B0001", 5.0, 7.0), ClipRef("B0002", 15.0, 19.0)]
    fitted = fit_narration(clips, BEATS, needed=9.0)
    # last: 15-19 -> 15-20 (+1); first: 5-7 -> 3-7 (+2)
    assert fitted.segments == [Segment(3.0, 7.0, 1.0), Segment(15.0, 20.0, 1.0)]
    assert fitted.fit == "ok"


def test_slow_motion_when_whole_beat_is_too_short() -> None:
    fitted = fit_narration([ClipRef("B0001", 0.0, 10.0)], BEATS, needed=11.0)
    assert fitted.segments[0].speed == pytest.approx(10.0 / 11.0)
    assert total(fitted.segments) == pytest.approx(11.0)
    assert fitted.fit == "ok"


def test_short_when_even_slow_motion_is_not_enough() -> None:
    fitted = fit_narration([ClipRef("B0001", 0.0, 10.0)], BEATS, needed=15.0)
    assert fitted.segments[0].speed == MIN_SPEED
    assert total(fitted.segments) < 15.0
    assert fitted.fit == "short"


def test_unknown_beat_raises() -> None:
    with pytest.raises(KeyError):
        fit_narration([ClipRef("B0999", 0.0, 1.0)], BEATS, needed=1.0)


# ---- build_timeline -------------------------------------------------------------------

def test_timeline_is_continuous_and_narration_length_is_tts_plus_pad() -> None:
    items = [
        {"id": "L0001", "type": "narrate", "text": "a", "clips": [{"beat": "B0001", "start": "00:00:01", "end": "00:00:07"}]},
        {"id": "L0002", "type": "original", "beat": "B0003", "start": "00:00:22", "end": "00:00:30"},
        {"id": "L0003", "type": "narrate", "text": "b", "clips": [{"beat": "B0002", "start": "00:00:11", "end": "00:00:14"}]},
    ]  # fmt: skip
    tts = {"L0001": ("tts/a.wav", 5.75), "L0003": ("tts/b.wav", 2.75)}

    timeline = build_timeline(items, BEATS, tts)

    assert [e.id for e in timeline] == ["L0001", "L0002", "L0003"]
    assert timeline[0].out_start == 0.0
    assert timeline[0].out_end == pytest.approx(5.75 + PAD_SEC)
    assert timeline[1].out_start == timeline[0].out_end
    assert timeline[1].out_end - timeline[1].out_start == pytest.approx(8.0)
    assert timeline[1].tts_file is None and timeline[1].segments == [Segment(22.0, 30.0, 1.0)]
    assert timeline[2].tts_file == "tts/b.wav" and timeline[2].tts_dur == 2.75
    assert [e.fit for e in timeline] == ["ok", "ok", "ok"]


def test_timeline_requires_tts_for_every_narration() -> None:
    items = [{"id": "L0001", "type": "narrate", "text": "a",
              "clips": [{"beat": "B0001", "start": "00:00:01", "end": "00:00:07"}]}]  # fmt: skip
    with pytest.raises(KeyError):
        build_timeline(items, BEATS, {})


def test_timeline_round_trips_through_json() -> None:
    from dataclasses import asdict

    from recap.fit import Segment, TimelineEntry, timeline_from_json

    timeline = [
        TimelineEntry("L0001", "narrate", 0.0, 3.25, [Segment(1.0, 2.0), Segment(5.0, 7.0, 0.9)], "tts/a.wav", 3.0, "trimmed"),
        TimelineEntry("L0002", "original", 3.25, 7.25, [Segment(10.0, 14.0)]),
    ]

    assert timeline_from_json(json.loads(json.dumps([asdict(e) for e in timeline]))) == timeline


def test_extension_never_makes_a_clip_longer_than_the_maximum() -> None:
    # Arrange: a 4 s clip in a 20 s beat, narration needs 12 s
    from recap.fit import MAX_SEGMENT_SEC

    # Act
    fitted = fit_narration([ClipRef("B0003", 22.0, 26.0)], BEATS, needed=12.0)

    # Assert: the clip grows only to MAX_SEGMENT_SEC; the rest is slow motion / reported short
    assert MAX_SEGMENT_SEC == 8.0
    assert all(s.src_end - s.src_start <= MAX_SEGMENT_SEC + 1e-6 for s in fitted.segments)
    assert fitted.fit == "short"
