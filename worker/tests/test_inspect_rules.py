"""Source inspection rules (pure): watermarks, burned-in subtitles, ads/credits, ranges to skip.

The fixtures mimic what OCR found on a real episode: a channel watermark in the bottom-left
corner of almost every frame, a title card and a sponsor card in the first seconds, background
signs read badly (low confidence), and "Behind the scenes" footage after the end card.
"""

from __future__ import annotations

import numpy as np
import pytest

from recap.inspect_rules import (
    FrameText,
    Region,
    SkipRange,
    TextBox,
    beats_to_skip,
    find_overlays,
    flag_frames,
    refine_region,
    skip_ranges,
    static_logos,
)
from recap.models import Beat

DURATION = 600.0


def box(text: str, x: float, y: float, w: float = 0.2, h: float = 0.04, conf: float = 1.0) -> TextBox:
    return TextBox(text=text, conf=conf, x=x, y=y, w=w, h=h)


WATERMARK = (box("youtube.com/FAPtivi", 0.14, 0.88), box("facebook.com/FAPtivi", 0.14, 0.92))


def film(extra: dict[int, tuple[TextBox, ...]] | None = None, watermark: bool = True) -> list[FrameText]:
    """One frame per second; every frame has the watermark, some have extra text."""
    extra = extra or {}
    return [FrameText(float(t), (WATERMARK if watermark else ()) + extra.get(t, ())) for t in range(int(DURATION))]


# ---- overlays ----------------------------------------------------------------------------------


def test_text_in_the_same_place_in_most_frames_is_a_watermark() -> None:
    overlays = find_overlays(film())

    assert [o.kind for o in overlays] == ["watermark"]
    region = overlays[0].region
    assert region.x <= 0.14 and region.x + region.w >= 0.34
    assert region.y <= 0.88 and region.y + region.h >= 0.96
    assert "youtube.com/faptivi" in overlays[0].text


def test_changing_text_in_the_same_place_is_subtitles_not_a_watermark() -> None:
    lines = {t: (box(f"câu thoại số {t}", 0.3, 0.85, w=0.4),) for t in range(0, int(DURATION), 2)}

    overlays = find_overlays(film(lines, watermark=False))

    assert [o.kind for o in overlays] == ["subtitles"]


def test_text_seen_only_now_and_then_is_no_overlay() -> None:
    signs = {t: (box("CAFE", 0.5, 0.3),) for t in range(0, 100)}  # 100 of 600 frames

    assert find_overlays(film(signs, watermark=False)) == []


def test_low_confidence_text_does_not_count() -> None:
    noise = film(watermark=False)
    frames = [FrameText(f.time, (box("COM", 0.05, 0.85, conf=0.3),)) for f in noise]

    assert find_overlays(frames) == []


# ---- static pixels -----------------------------------------------------------------------------


def static_map(height: int = 90, width: int = 160, logo: tuple[int, int, int, int] | None = None) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    if logo:
        x0, y0, x1, y1 = logo
        mask[y0:y1, x0:x1] = True
    return mask


def test_refine_grows_the_text_region_to_the_whole_static_logo() -> None:
    # the OCR saw only the text right of a round logo; the logo's pixels never change
    static = static_map(logo=(8, 73, 54, 90))  # x 0.05–0.34, y 0.81–1.0
    text_region = Region(0.14, 0.88, 0.2, 0.08)

    region = refine_region(text_region, static)

    assert region.x <= 0.05 and region.y <= 0.81 and region.x + region.w >= 0.34 and region.y + region.h >= 0.99


def test_refine_without_static_pixels_keeps_the_region_padded() -> None:
    region = refine_region(Region(0.4, 0.4, 0.1, 0.1), static_map())

    assert region.x == pytest.approx(0.39) and region.w == pytest.approx(0.12)


def test_static_logo_without_text_is_found_in_a_corner() -> None:
    static = static_map(logo=(140, 4, 156, 16))  # top-right badge

    logos = static_logos(static)

    assert len(logos) == 1 and logos[0].x > 0.8 and logos[0].y < 0.1


def test_a_stray_static_patch_does_not_grow_the_logo() -> None:
    static = static_map(logo=(140, 4, 156, 16))
    static[22:25, 114:117] = True  # a few pixels of a still background, same corner, far from the badge

    logos = static_logos(static)

    assert len(logos) == 1 and logos[0].x > 0.8 and logos[0].h < 0.2


def test_tiny_static_specks_are_not_logos() -> None:
    assert static_logos(static_map(logo=(150, 5, 152, 7))) == []


# ---- flags -------------------------------------------------------------------------------------


def test_sponsor_title_and_credit_cards_are_flagged() -> None:
    frames = film({
        5: (box("đạo diễn: TRẦN ĐỨC VIÊN", 0.3, 0.4), box("TẬP 136", 0.4, 0.5)),
        9: (box("TRUNG NGUYÊN LEGEND", 0.3, 0.4), box("The Energy Coffee That Changes Life", 0.3, 0.5)),
        300: (box("Nhà tài trợ: ABC", 0.6, 0.1),),
        310: (box("w88.bet", 0.6, 0.1),),
    })  # fmt: skip

    flags = flag_frames(frames, find_overlays(frames), DURATION)

    kinds = {(f.time, f.kind) for f in flags}
    assert (5.0, "credit") in kinds and (9.0, "title") in kinds
    assert (300.0, "ad") in kinds and (310.0, "ad") in kinds


def test_watermark_domains_and_blurry_signs_are_not_flagged() -> None:
    frames = film({200: (box("CƠ CHU T", 0.5, 0.2, conf=0.3), box("HAY", 0.6, 0.2, conf=0.3))})

    assert flag_frames(frames, find_overlays(frames), DURATION) == []


def test_text_in_the_middle_of_the_film_needs_a_keyword() -> None:
    frames = film({300: (box("TRƯỞNG PHÒNG", 0.3, 0.3), box("PHÒNG HỌP", 0.6, 0.3))})

    assert flag_frames(frames, find_overlays(frames), DURATION) == []


def test_dialogue_in_burned_in_subtitles_is_not_an_ad() -> None:
    lines = {t: (box(f"câu thoại số {t}", 0.3, 0.85, w=0.4),) for t in range(0, int(DURATION), 2)}
    lines[300] = (box("anh ấy là nhà tài trợ của em", 0.3, 0.85, w=0.4),)
    frames = film(lines, watermark=False)

    assert flag_frames(frames, find_overlays(frames), DURATION) == []


# ---- ranges and beats --------------------------------------------------------------------------


def test_end_card_then_behind_the_scenes_skips_to_the_end() -> None:
    frames = film({
        **{t: (box("đạo diễn: TRẦN ĐỨC VIÊN", 0.3, 0.4),) for t in (510, 511)},
        **{t: (box("Behind the scenes", 0.05, 0.05),) for t in range(520, 590, 2)},
    })  # fmt: skip

    ranges = skip_ranges(flag_frames(frames, find_overlays(frames), DURATION), DURATION)

    assert ranges[-1].start == pytest.approx(509.5) and ranges[-1].end == DURATION


def test_opening_cards_skip_from_the_start() -> None:
    frames = film({**{t: (box("đạo diễn: A", 0.3, 0.4),) for t in (5, 6)},
                   **{t: (box("Nhà tài trợ: B", 0.3, 0.4),) for t in (11, 12)}})  # fmt: skip

    ranges = skip_ranges(flag_frames(frames, find_overlays(frames), DURATION), DURATION)

    assert ranges[0] == SkipRange(0.0, 12.5, ranges[0].reason)
    assert "đạo diễn" in ranges[0].reason.lower() or "tài trợ" in ranges[0].reason.lower()


def test_an_ad_in_the_middle_skips_only_around_it() -> None:
    frames = film({t: (box("Nhà tài trợ: ABC", 0.6, 0.1),) for t in (300, 301, 302)})

    ranges = skip_ranges(flag_frames(frames, find_overlays(frames), DURATION), DURATION)

    assert ranges == [SkipRange(299.5, 302.5, ranges[0].reason)] and "tài trợ" in ranges[0].reason.lower()


@pytest.mark.parametrize("second", [60, 300, 540])
def test_text_read_in_a_single_frame_skips_nothing(second: int) -> None:
    # a street sign or a phone screen saying "diễn viên" / "quảng cáo" for one sampled second, at the
    # start, middle or end: real cards stay on screen for several seconds
    frames = film({second: (box("Tuyển diễn viên quảng cáo", 0.3, 0.4),)})

    assert skip_ranges(flag_frames(frames, find_overlays(frames), DURATION), DURATION) == []


def test_flags_far_apart_do_not_corroborate_each_other() -> None:
    frames = film({t: (box("đạo diễn", 0.3, 0.4),) for t in (480, 500, 520)})

    assert skip_ranges(flag_frames(frames, find_overlays(frames), DURATION), DURATION) == []


@pytest.mark.parametrize("text", ["Behind you!", "Cast away"])
def test_ambiguous_english_words_are_not_keywords(text: str) -> None:
    frames = film({t: (box(text, 0.3, 0.4),) for t in (300, 301, 302)})

    assert flag_frames(frames, find_overlays(frames), DURATION) == []


def test_beats_touching_a_skip_range_are_skipped_with_the_reason() -> None:
    beats = [Beat(id=f"B{i}", start=s, end=e, shots=[(s, e)], keyframe="k.jpg")
             for i, (s, e) in enumerate([(0, 10), (10, 20), (20, 30)], start=1)]  # fmt: skip
    ranges = [SkipRange(0.0, 12.5, "credit: đạo diễn"), SkipRange(29.9, 40.0, "ad: w88.bet")]

    assert beats_to_skip(beats, ranges) == {"B1": "credit: đạo diễn", "B2": "credit: đạo diễn", "B3": "ad: w88.bet"}


def test_static_mask_keeps_a_logo_that_is_missing_from_some_frames() -> None:
    from recap.inspect_rules import static_mask

    # Arrange: moving footage; a bright logo in 85% of the frames (not in the intro/outtakes);
    # black letterbox rows that never change
    rng = np.random.default_rng(1)
    stack = rng.uniform(30, 230, size=(100, 20, 30)).astype(np.float32)
    stack[:85, 14:18, 2:8] = 200.0
    stack[:, :2, :] = 0.0

    # Act
    static = static_mask(stack)

    # Assert
    assert static[14:18, 2:8].all()
    assert not static[:2, :].any()  # black bars are not a logo
    assert static.sum() == 4 * 6
