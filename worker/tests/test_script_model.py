import pytest

from recap.digest import digest_text, split_parts
from recap.models import DescribedBeat
from recap.script_model import (
    PAD_SEC,
    estimate_duration,
    format_tc,
    parse_response,
    parse_tc,
    validate_items,
)


def beat(i: int, start: float, end: float, desc: str = "Cóc ngồi", dialogue: str = "") -> DescribedBeat:
    bid = f"B{i:04d}"
    return DescribedBeat(id=bid, start=start, end=end, shots=[(start, end)], keyframe=f"keyframes/{bid}.jpg",
                         dialogue=dialogue, desc=desc)  # fmt: skip


BEATS = {b.id: b for b in [beat(1, 0.0, 10.0), beat(2, 10.0, 25.0), beat(3, 25.0, 31.5)]}


# ---- timecodes -------------------------------------------------------------------

@pytest.mark.parametrize("text,sec", [("00:41:05.2", 2465.2), ("41:05.2", 2465.2), ("5.5", 5.5), (12, 12.0)])
def test_parse_tc(text: str | int, sec: float) -> None:
    assert parse_tc(text) == pytest.approx(sec)


@pytest.mark.parametrize("bad", ["", "abc", "1:2:3:4", "00:61:00", None])
def test_parse_tc_rejects_garbage(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_tc(bad)  # type: ignore[arg-type]


def test_format_tc() -> None:
    assert format_tc(2465.2) == "00:41:05.2"
    assert format_tc(0) == "00:00:00"
    assert format_tc(3725.125) == "01:02:05.125"


# ---- digest ---------------------------------------------------------------------

def test_digest_line_format() -> None:
    b = beat(123, 2465.0, 2477.0, desc="Minh cãi nhau với bố trong bếp", dialogue="Con không về nữa đâu")
    assert digest_text([b]) == '[B0123 00:41:05-00:41:17] Hình: Minh cãi nhau với bố trong bếp | Thoại: "Con không về nữa đâu"'


def test_digest_without_dialogue() -> None:
    assert digest_text([beat(1, 0.0, 4.5)]).endswith("| Thoại: (không có)")


def test_split_parts_by_duration() -> None:
    beats = [beat(i + 1, i * 600.0, (i + 1) * 600.0) for i in range(7)]  # 70 minutes of 10-min beats
    parts = split_parts(beats, part_size_min=30)
    assert [[b.id for b in p] for p in parts] == [
        ["B0001", "B0002", "B0003"], ["B0004", "B0005", "B0006"], ["B0007"]
    ]


def test_split_parts_single_short_film() -> None:
    assert len(split_parts(list(BEATS.values()), part_size_min=30)) == 1


# ---- response parsing ---------------------------------------------------------------

RESPONSE = """Đây là kịch bản:
```yaml
items:
  - id: L0001
    type: narrate
    text: "Mười năm sau, cóc quyết lên trời."
    clips:
      - {beat: B0001, start: "00:00:01.0", end: "00:00:06.0"}
```
SUMMARY: Cóc lên trời kiện.
Trời thua.
"""


def test_parse_response_extracts_yaml_and_summary() -> None:
    data, summary = parse_response(RESPONSE)
    assert data["items"][0]["clips"][0]["beat"] == "B0001"
    assert summary == "Cóc lên trời kiện. Trời thua."


def test_parse_response_without_fence() -> None:
    data, summary = parse_response("items:\n  - {type: original, beat: B0002, start: 11, end: 18}\nSUMMARY: x")
    assert data["items"][0]["type"] == "original" and summary == "x"


def test_parse_response_bad_yaml_raises() -> None:
    with pytest.raises(ValueError):
        parse_response("```yaml\nitems: [unclosed\n```")


# ---- validation -------------------------------------------------------------------

def narrate(text: str, *clips: tuple[str, object, object]) -> dict:
    return {"type": "narrate", "text": text, "clips": [{"beat": b, "start": s, "end": e} for b, s, e in clips]}


def test_valid_items_are_normalized() -> None:
    raw = {"items": [
        narrate("một hai ba bốn năm sáu bảy tám chín mười", ("B0001", "00:00:01", "00:00:04.5")),
        {"type": "original", "beat": "B0002", "start": "00:00:12", "end": "00:00:20", "note": "cao trào"},
    ]}  # fmt: skip
    items, errors = validate_items(raw, BEATS, target_sec=11.0)
    assert errors == []
    assert items[0] == {"type": "narrate", "text": "một hai ba bốn năm sáu bảy tám chín mười",
                        "clips": [{"beat": "B0001", "start": "00:00:01", "end": "00:00:04.5"}]}  # fmt: skip
    assert items[1]["note"] == "cao trào"


def test_unknown_beat_and_out_of_range_times_are_reported() -> None:
    raw = [narrate("một hai ba", ("B0099", 1, 2)), narrate("bốn năm sáu", ("B0001", 8, 12))]
    _, errors = validate_items(raw, BEATS, target_sec=2.0)
    assert any("B0099" in e and "không có" in e for e in errors)
    assert any("B0001" in e and "ngoài" in e for e in errors)


def test_small_overshoot_is_clamped_to_beat() -> None:
    raw = [narrate("một hai ba", ("B0001", 8.0, 10.2))]
    items, errors = validate_items(raw, BEATS, target_sec=1.25)  # 3 words / 3 + PAD_SEC
    assert errors == [] and items[0]["clips"][0]["end"] == "00:00:10"


def test_structural_errors() -> None:
    raw = [{"type": "narrate", "text": "", "clips": []}, {"type": "music"}, {"type": "original", "beat": "B0001",
                                                                                "start": 5, "end": 3}]  # fmt: skip
    _, errors = validate_items(raw, BEATS, target_sec=10.0)
    assert len(errors) >= 3


def test_not_a_list() -> None:
    _, errors = validate_items({"foo": 1}, BEATS, target_sec=10.0)
    assert errors and "items" in errors[0]


def test_duration_outside_tolerance_is_an_error() -> None:
    raw = [narrate("một hai ba", ("B0001", 0, 3))]  # ≈1 s of narration
    _, errors = validate_items(raw, BEATS, target_sec=60.0)
    assert any("Tổng thời lượng" in e for e in errors)


def test_estimate_duration() -> None:
    items = [
        {"type": "narrate", "text": "a b c d e f", "clips": []},
        {"type": "original", "beat": "B0002", "start": "00:00:12", "end": "00:00:20"},
    ]
    # narration = words / rate + the pad the assembler adds after every narrate line
    assert estimate_duration(items) == pytest.approx(2.0 + PAD_SEC + 8.0)
    assert estimate_duration(items, words_per_sec=6.0) == pytest.approx(1.0 + PAD_SEC + 8.0)


def test_duration_check_uses_given_speaking_rate() -> None:
    raw = [narrate(" ".join(["từ"] * 30), ("B0001", 0, 3))]  # 30 words: 10 s at 3 w/s, 6 s at 5 w/s

    assert validate_items(raw, BEATS, target_sec=10.0)[1] == []
    _, errors = validate_items(raw, BEATS, target_sec=10.0, words_per_sec=5.0)
    assert any("Tổng thời lượng ước tính 6 giây" in e for e in errors)
