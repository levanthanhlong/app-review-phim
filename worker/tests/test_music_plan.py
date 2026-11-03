"""Splitting the recap into music cues and checking the English music prompts (pure, no I/O)."""

from __future__ import annotations

from recap.fit import Segment, TimelineEntry
from recap.music_plan import (
    MIN_CUE_SEC,
    banned_terms,
    cue_input,
    prompt_problems,
    validate_cues,
)


def entry(n: int, start: float, end: float, kind: str = "narrate") -> TimelineEntry:
    return TimelineEntry(f"L{n:04d}", kind, start, end, [Segment(start, end)])  # type: ignore[arg-type]


# 6 lines, 120 s in total: lines 1-3 end at 60 s, lines 4-6 at 120 s.
TIMELINE = [entry(1, 0, 20), entry(2, 20, 40), entry(3, 40, 60, "original"), entry(4, 60, 80), entry(5, 80, 100),
            entry(6, 100, 120)]  # fmt: skip
BANNED = banned_terms("Cóc Kiện Trời", ["Minh trở về nhà, gặp lại bà Lan.", "Cóc lên trời."])


def cue(first: str, last: str, prompt: str = "dark ambient pads, slow pulse", alt: str = "sparse piano, cold") -> dict:
    return {"from": first, "to": last, "mood": "căng thẳng", "prompt": prompt, "alt_prompt": alt}


# ---- input for the LLM ------------------------------------------------------------------

def test_cue_input_lists_every_line_with_its_time_and_kind() -> None:
    text = cue_input(TIMELINE[:3], {"L0001": "Cóc ra đi.", "L0002": "Gặp cua."}, {"L0003": "Đồng ý"})

    assert text.splitlines() == [
        "[L0001 00:00-00:20 kể] Cóc ra đi.",
        "[L0002 00:20-00:40 kể] Gặp cua.",
        '[L0003 00:40-01:00 thoại gốc] "Đồng ý"',
    ]


# ---- names that must not reach the music prompt ---------------------------------------------

def test_banned_terms_are_title_words_and_names_inside_sentences() -> None:
    # Arrange / Act: see BANNED
    # Assert: accents are dropped so "Coc" in an English prompt is caught too
    assert {"coc", "kien", "troi", "lan"} <= BANNED
    assert "minh" not in BANNED  # first word of a sentence: capitalized anyway, not proof of a name


def test_clean_english_prompt_has_no_problems() -> None:
    assert prompt_problems("tense orchestral strings, 90 bpm, building percussion", BANNED) == []


def test_prompt_with_a_title_word_or_name_is_rejected() -> None:
    problems = prompt_problems("heroic theme for Coc, the toad", BANNED)

    assert problems and "coc" in problems[0].lower()


def test_prompt_with_vietnamese_or_artist_style_is_rejected() -> None:
    assert prompt_problems("nhạc buồn, piano", BANNED)
    assert prompt_problems("ambient in the style of a famous composer", BANNED)


# ---- cue validation ---------------------------------------------------------------------

def test_two_contiguous_cues_covering_the_recap_are_valid() -> None:
    cues, errors = validate_cues({"cues": [cue("L0001", "L0003"), cue("L0004", "L0006")]}, TIMELINE, BANNED)

    assert errors == []
    assert [(c.id, c.first_line, c.last_line, c.out_start, c.out_end) for c in cues] == [
        ("M01", "L0001", "L0003", 0, 60),
        ("M02", "L0004", "L0006", 60, 120),
    ]


def test_gap_between_cues_is_an_error() -> None:
    _, errors = validate_cues({"cues": [cue("L0001", "L0002"), cue("L0004", "L0006")]}, TIMELINE, BANNED)

    assert any("L0003" in e for e in errors)


def test_cues_must_reach_the_last_line() -> None:
    _, errors = validate_cues({"cues": [cue("L0001", "L0003"), cue("L0004", "L0005")]}, TIMELINE, BANNED)

    assert any("L0006" in e for e in errors)


def test_cue_shorter_than_minimum_is_an_error() -> None:
    _, errors = validate_cues({"cues": [cue("L0001", "L0001"), cue("L0002", "L0006")]}, TIMELINE, BANNED)

    assert any(str(MIN_CUE_SEC) in e for e in errors)


def test_single_cue_may_be_short_when_the_whole_recap_is_short() -> None:
    _, errors = validate_cues({"cues": [cue("L0001", "L0002")]}, TIMELINE[:2], BANNED)

    assert errors == []


def test_unsafe_prompt_inside_a_cue_is_reported_with_the_cue_number() -> None:
    raw = {"cues": [cue("L0001", "L0003", alt="theme for Lan"), cue("L0004", "L0006")]}

    _, errors = validate_cues(raw, TIMELINE, BANNED)

    assert any(e.startswith("Phần nhạc 1") and "lan" in e.lower() for e in errors)


def test_missing_cue_list_is_an_error() -> None:
    _, errors = validate_cues({"items": []}, TIMELINE, BANNED)

    assert errors
