"""Ads shown over the film (a betting site in a corner, a sponsor's hotline): blurred while they show,
the scene is kept. Ads that fill the screen are cards: their beats are skipped (inspect_rules)."""

from __future__ import annotations

import pytest

from recap.ad_blurs import TimedBlur, ad_blurs, outside, split_ads
from recap.inspect_rules import FrameText, Region, TextBox, find_overlays, flag_frames, skip_ranges

DURATION = 600.0


def box(text: str, x: float, y: float, w: float = 0.2, h: float = 0.04) -> TextBox:
    return TextBox(text=text, conf=1.0, x=x, y=y, w=w, h=h)


def film(extra: dict[int, tuple[TextBox, ...]]) -> list[FrameText]:
    return [FrameText(float(t), extra.get(t, ())) for t in range(int(DURATION))]


def inspect(frames: list[FrameText]) -> tuple[list, list[TimedBlur]]:
    flags = flag_frames(frames, find_overlays(frames), DURATION)
    overlay_ads, others = split_ads(flags)
    return skip_ranges(others, DURATION), ad_blurs(overlay_ads)


def test_an_ad_in_a_corner_is_blurred_while_it_shows_and_the_scene_is_kept() -> None:
    # Arrange: "w88.bet" in the top-right corner from 300 s to 305 s
    frames = film({t: (box("Nhà cái w88.bet", 0.75, 0.05),) for t in range(300, 306)})

    # Act
    ranges, blurs = inspect(frames)

    # Assert
    assert ranges == []
    assert len(blurs) == 1
    blur = blurs[0]
    assert (blur.start, blur.end) == (299.5, 305.5) and "w88" in blur.text
    assert blur.region.x < 0.75 and blur.region.x + blur.region.w > 0.95 and blur.region.y <= 0.05


def test_an_ad_near_the_start_is_blurred_not_a_reason_to_skip_the_opening() -> None:
    frames = film({t: (box("Hotline 1900 1234", 0.02, 0.9),) for t in (60, 61, 62)})

    ranges, blurs = inspect(frames)

    assert ranges == [] and [(b.start, b.end) for b in blurs] == [(59.5, 62.5)]


def test_an_ad_filling_the_screen_is_a_card_whose_scene_is_skipped() -> None:
    frames = film({t: (box("Nhà tài trợ: ABC", 0.3, 0.45, w=0.4, h=0.1),) for t in (300, 301, 302)})

    ranges, blurs = inspect(frames)

    assert blurs == [] and [(r.start, r.end) for r in ranges] == [(299.5, 302.5)]


def test_ads_in_two_places_or_far_apart_in_time_are_separate_blurs() -> None:
    frames = film({
        **{t: (box("w88.bet", 0.75, 0.05), box("hotline 1900", 0.02, 0.9)) for t in (100, 101)},
        **{t: (box("w88.bet", 0.75, 0.05),) for t in (200, 201)},
    })  # fmt: skip

    _, blurs = inspect(frames)

    assert sorted((b.start, round(b.region.y, 1)) for b in blurs) == [(99.5, 0.0), (99.5, 0.9), (199.5, 0.0)]


def test_an_ad_read_in_one_frame_only_is_not_blurred() -> None:
    _, blurs = inspect(film({300: (box("w88.bet", 0.75, 0.05),)}))

    assert blurs == []


@pytest.mark.parametrize("region", [Region(0.75, 0.05, 0.2, 0.04), Region(0.0, 0.9, 0.3, 0.05)])
def test_blur_regions_stay_inside_the_frame(region: Region) -> None:
    frames = film({t: (box("w88.bet", region.x, region.y, region.w, region.h),) for t in (10, 11)})

    _, blurs = inspect(frames)

    r = blurs[0].region
    assert r.x >= 0 and r.y >= 0 and r.x + r.w <= 1 and r.y + r.h <= 1


def test_ads_inside_a_skipped_range_need_no_blur() -> None:
    frames = film({
        **{t: (box("đạo diễn: A", 0.3, 0.05), box("faptvgroup@gmail.com", 0.35, 0.9)) for t in range(5, 9)},
        **{t: (box("w88.bet", 0.75, 0.05),) for t in (300, 301)},
    })  # fmt: skip

    ranges, blurs = inspect(frames)

    assert [r.start for r in ranges] == [0.0]
    assert [b.start for b in outside(blurs, ranges)] == [299.5]
