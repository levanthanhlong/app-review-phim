"""Channel branding: thin bands above/below the film (or a see-through banner over it), the frame
image, the style file (img/brand.json) and where things go."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from recap.branding import (
    BG_COLOR,
    BrandingError,
    BrandStyle,
    brand_layout,
    brand_signature,
    build_frame,
    check_image,
    load_style,
)
from recap.fit import Segment, TimelineEntry
from recap.render import PREVIEW, item_args

BANNER_SIZE = (2160, 260)
YELLOW = (255, 201, 60, 255)


def make_images(tmp_path: Path) -> tuple[Path, Path]:
    banner = Image.new("RGBA", BANNER_SIZE, BG_COLOR + (255,))
    banner.paste(YELLOW, (0, 0, BANNER_SIZE[0], 40))  # yellow film strip on top, like the real one
    banner.paste((255, 255, 255, 255), (1000, 120, 1160, 200))  # the channel name, say
    logo = Image.new("RGBA", (800, 800), YELLOW)
    banner_path, logo_path = tmp_path / "banner.png", tmp_path / "logo.png"
    banner.save(banner_path)
    logo.save(logo_path)
    return banner_path, logo_path


# ---- layout ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (854, 480)])
def test_default_bands_are_thin_and_the_film_fills_the_rest(width: int, height: int) -> None:
    layout = brand_layout(BANNER_SIZE, width, height)

    assert 0 < layout.top <= height * 0.02
    assert height * 0.07 <= layout.bottom <= height * 0.08
    # the film covers the whole width between the bands: no black bars, at least 85% of the frame
    assert (layout.video_x, layout.video_w) == (0, width)
    assert layout.video_y == layout.top and layout.video_y + layout.video_h == height - layout.bottom
    assert layout.fill and layout.film_area >= 0.85
    assert all(v % 2 == 0 for v in (layout.top, layout.bottom, layout.video_w, layout.video_h, layout.video_y))


def test_banner_is_scaled_to_the_band_height_keeping_its_aspect() -> None:
    layout = brand_layout(BANNER_SIZE, 1920, 1080)

    assert layout.banner_w == pytest.approx(layout.bottom * 2160 / 260, abs=2)
    assert layout.banner_x == pytest.approx((1920 - layout.banner_w) / 2, abs=1)  # centred by default


def test_style_can_drop_the_top_band_and_move_the_banner() -> None:
    style = BrandStyle(top_ratio=0, banner_align="left", bottom_ratio=0.06)

    layout = brand_layout(BANNER_SIZE, 1920, 1080, style)

    assert layout.top == 0 and layout.banner_x == 0
    assert layout.bottom == pytest.approx(1080 * 0.06, abs=2)


def test_overlay_puts_the_film_under_the_whole_frame() -> None:
    layout = brand_layout(BANNER_SIZE, 1920, 1080, BrandStyle(overlay=True))

    assert (layout.video_x, layout.video_y, layout.video_w, layout.video_h) == (0, 0, 1920, 1080)
    assert layout.film_area >= 0.9  # only the see-through banner covers the film


def test_fit_mode_keeps_the_whole_picture() -> None:
    layout = brand_layout(BANNER_SIZE, 1920, 1080, BrandStyle(video_fit="fit"))

    assert not layout.fill


# ---- style file ------------------------------------------------------------------------------


def test_style_is_read_from_brand_json_next_to_the_banner(tmp_path: Path) -> None:
    banner, _ = make_images(tmp_path)
    (tmp_path / "brand.json").write_text(json.dumps({"bottom_ratio": 0.07, "overlay": True, "overlay_opacity": 0.5}))

    style = load_style(banner)

    assert style == BrandStyle(bottom_ratio=0.07, overlay=True, overlay_opacity=0.5)


def test_missing_style_file_means_defaults(tmp_path: Path) -> None:
    banner, _ = make_images(tmp_path)

    assert load_style(banner) == BrandStyle()


@pytest.mark.parametrize("content", ["{not json", '{"bottom_ratio": 0.5}', '{"banner_align": "middle"}', '{"thickness": 1}'])
def test_invalid_style_file_is_reported(tmp_path: Path, content: str) -> None:
    banner, _ = make_images(tmp_path)
    (tmp_path / "brand.json").write_text(content)

    with pytest.raises(BrandingError, match="brand.json"):
        load_style(banner)


def test_editing_the_style_changes_the_signature(tmp_path: Path) -> None:
    banner, logo = make_images(tmp_path)
    before = brand_signature(str(banner), str(logo))

    (tmp_path / "brand.json").write_text('{"top_ratio": 0}')

    assert brand_signature(str(banner), str(logo)) != before


# ---- frame image -----------------------------------------------------------------------------


def test_boxed_frame_is_opaque_on_the_bands_and_clear_over_the_film(tmp_path: Path) -> None:
    banner, logo = make_images(tmp_path)
    layout = brand_layout(BANNER_SIZE, 1920, 1080)

    frame = Image.open(build_frame(banner, logo, layout, BrandStyle(), tmp_path / "frame.png"))

    assert frame.size == (1920, 1080)
    assert frame.getpixel((960, 540))[3] == 0  # film shows through
    assert frame.getpixel((5, 1075))[3] == 255  # band left of the banner: banner background colour
    assert frame.getpixel((960, 1075))[3] == 255  # the banner itself
    assert frame.getpixel((5, layout.top // 2))[:3] == YELLOW[:3]  # thin film strip on top


def test_overlay_frame_shows_the_film_through_the_banner_background(tmp_path: Path) -> None:
    banner, logo = make_images(tmp_path)
    style = BrandStyle(overlay=True, overlay_opacity=0.6, top_ratio=0, logo_corner=None)
    layout = brand_layout(BANNER_SIZE, 1920, 1080, style)

    frame = Image.open(build_frame(banner, logo, layout, style, tmp_path / "frame.png"))

    background = frame.getpixel((layout.banner_x + 20, 1080 - 10))[3]
    name_x = layout.banner_x + round(1080 / 2160 * layout.banner_w)
    name_y = 1080 - layout.bottom + round(160 / 260 * layout.bottom)
    assert background == pytest.approx(0.6 * 255, abs=3)
    assert frame.getpixel((name_x, name_y))[3] == 255  # the channel name stays solid


def test_logo_sits_small_in_the_chosen_corner_of_the_film(tmp_path: Path) -> None:
    banner, logo = make_images(tmp_path)
    style = BrandStyle(logo_corner="top-right", logo_ratio=0.07)
    layout = brand_layout(BANNER_SIZE, 1920, 1080, style)

    frame = Image.open(build_frame(banner, logo, layout, style, tmp_path / "frame.png"))

    diameter = round(1080 * 0.07)
    box = frame.getbbox()  # everything drawn; the logo is the only thing over the film area
    film = frame.crop((0, layout.video_y + 1, 1920, layout.video_y + layout.video_h - 1))
    logo_box = film.getbbox()
    assert box is not None and logo_box is not None
    assert logo_box[2] - logo_box[0] == pytest.approx(diameter, abs=2)
    assert logo_box[0] > 1920 * 0.9 and logo_box[1] < 1080 * 0.06


# ---- render ----------------------------------------------------------------------------------


def test_render_fills_the_box_cropping_the_overflow_and_overlays_the_frame() -> None:
    layout = brand_layout(BANNER_SIZE, PREVIEW.width, PREVIEW.height)
    entry = TimelineEntry("L1", "original", 0.0, 4.0, [Segment(10.0, 14.0)])

    args = item_args(entry, Path("src.mp4"), None, PREVIEW, 25.0, 0.0, True, True, Path("o.mkv"),
                     brand=(layout, Path("frame.png")))  # fmt: skip

    graph = args[args.index("-filter_complex") + 1]
    assert f"scale={layout.video_w}:{layout.video_h}:force_original_aspect_ratio=increase:flags=lanczos" in graph
    assert f"crop={layout.video_w}:{layout.video_h}" in graph
    assert f"pad={PREVIEW.width}:{PREVIEW.height}:{layout.video_x}:{layout.video_y}" in graph
    assert "frame.png" in args and "[1:v]overlay=0:0" in graph


def test_render_fit_mode_letterboxes_inside_the_box() -> None:
    layout = brand_layout(BANNER_SIZE, PREVIEW.width, PREVIEW.height, BrandStyle(video_fit="fit"))
    entry = TimelineEntry("L1", "original", 0.0, 4.0, [Segment(10.0, 14.0)])

    args = item_args(entry, Path("src.mp4"), None, PREVIEW, 25.0, 0.0, True, True, Path("o.mkv"),
                     brand=(layout, Path("frame.png")))  # fmt: skip

    graph = args[args.index("-filter_complex") + 1]
    assert "force_original_aspect_ratio=decrease:flags=lanczos" in graph and "crop=" not in graph


def test_check_image_rejects_missing_or_non_image_files(tmp_path: Path) -> None:
    text = tmp_path / "a.txt"
    text.write_text("x")

    with pytest.raises(BrandingError):
        check_image(str(tmp_path / "missing.png"))
    with pytest.raises(BrandingError):
        check_image(str(text))
