"""Channel branding: banner bands above/below the film, the frame image, and where things go."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from recap.branding import (
    BG_COLOR,
    BrandingError,
    brand_layout,
    build_frame,
    check_image,
)
from recap.fit import Segment, TimelineEntry
from recap.render import PREVIEW, item_args

BANNER_SIZE = (2160, 260)


def make_images(tmp_path: Path) -> tuple[Path, Path]:
    banner = Image.new("RGBA", BANNER_SIZE, BG_COLOR + (255,))
    banner.paste((255, 201, 60, 255), (0, 0, BANNER_SIZE[0], 40))  # yellow film strip on top, like the real one
    logo = Image.new("RGBA", (800, 800), (255, 201, 60, 255))
    banner_path, logo_path = tmp_path / "banner.png", tmp_path / "logo.png"
    banner.save(banner_path)
    logo.save(logo_path)
    return banner_path, logo_path


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (854, 480)])
def test_layout_keeps_the_film_16_9_between_the_bands(width: int, height: int) -> None:
    layout = brand_layout(BANNER_SIZE, width, height)

    assert layout.bottom == pytest.approx(width * 260 / 2160, abs=2)  # banner keeps its aspect at full width
    assert layout.top > 0 and layout.top + layout.video_h + layout.bottom <= height
    assert layout.video_w / layout.video_h == pytest.approx(16 / 9, abs=0.02)
    assert abs(layout.video_x - (width - layout.video_w) / 2) <= 1  # centered (even pixels for yuv420p)
    assert all(v % 2 == 0 for v in (layout.video_w, layout.video_h, layout.video_x, layout.video_y))


def test_frame_is_opaque_on_the_bands_and_clear_over_the_film(tmp_path: Path) -> None:
    banner, logo = make_images(tmp_path)
    layout = brand_layout(BANNER_SIZE, 854, 480)

    frame = Image.open(build_frame(banner, logo, layout, tmp_path / "frame.png"))

    assert frame.size == (854, 480)
    assert frame.getpixel((427, layout.video_y + layout.video_h // 2))[3] == 0  # film shows through
    assert frame.getpixel((427, 479))[3] == 255  # bottom banner
    assert frame.getpixel((5, 2))[3] == 255  # top band
    assert frame.getpixel((427, layout.video_y + layout.video_h - 1))[3] == 0


def test_render_places_the_film_in_its_box_and_overlays_the_frame() -> None:
    layout = brand_layout(BANNER_SIZE, PREVIEW.width, PREVIEW.height)
    entry = TimelineEntry("L1", "original", 0.0, 4.0, [Segment(10.0, 14.0)])

    args = item_args(entry, Path("src.mp4"), None, PREVIEW, 25.0, 0.15, True, True, Path("o.mkv"),
                     brand=(layout, Path("frame.png")))  # fmt: skip

    graph = args[args.index("-filter_complex") + 1]
    assert f"scale={layout.video_w}:{layout.video_h}:force_original_aspect_ratio=decrease" in graph
    assert f"pad={PREVIEW.width}:{PREVIEW.height}:(ow-iw)/2:{layout.video_y}+({layout.video_h}-ih)/2" in graph
    assert "frame.png" in args and "[1:v]overlay=0:0" in graph


def test_check_image_rejects_missing_or_non_image_files(tmp_path: Path) -> None:
    text = tmp_path / "a.txt"
    text.write_text("x")

    with pytest.raises(BrandingError):
        check_image(str(tmp_path / "missing.png"))
    with pytest.raises(BrandingError):
        check_image(str(text))
