"""Channel branding: the film sits in a 16:9 box between a top band and the channel banner.

Bottom: the banner image at full frame width (it keeps its aspect). Top: a band of the banner's
background colour, with the banner's yellow film strip along the edge that touches the film and
the round channel logo on the right. Everything is drawn once per frame size into a transparent
PNG that the renderer lays over each clip; the film is scaled into the box (`BrandLayout`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, UnidentifiedImageError

from recap import config

BG_COLOR = (26, 20, 35)  # background of the "Mắm review phim" banner
TOP_BAND_RATIO = 0.09  # of the frame height
STRIP_RATIO = 40 / 260  # height of the film strip along the banner's top edge, of the banner height
LOGO_MARGIN_RATIO = 0.06  # of the top band height
LOGO_RIGHT_RATIO = 0.03  # logo's distance from the right edge, of the frame width
VIDEO_ASPECT = 16 / 9
BRAND_VERSION = "2"  # bump when the frame drawing changes, to re-render cached clips


class BrandingError(Exception):
    pass


@dataclass(frozen=True)
class BrandLayout:
    width: int
    height: int
    top: int  # band above the film
    bottom: int  # banner below the film
    video_w: int
    video_h: int
    video_x: int
    video_y: int


def _even(value: float) -> int:
    return int(value) // 2 * 2


def check_image(text: str) -> Path:
    path = Path(text).expanduser()
    if not path.is_file():
        raise BrandingError(f"Không tìm thấy ảnh {path}")
    try:
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise BrandingError(f"{path.name} không phải file ảnh đọc được") from exc
    return path


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def brand_layout(banner_size: tuple[int, int], width: int, height: int) -> BrandLayout:
    banner_w, banner_h = banner_size
    bottom = _even(round(width * banner_h / banner_w))
    top = _even(round(height * TOP_BAND_RATIO))
    room = height - top - bottom
    video_h = _even(min(room, width / VIDEO_ASPECT))
    video_w = _even(video_h * VIDEO_ASPECT)
    return BrandLayout(width, height, top, bottom, video_w, video_h,
                       video_x=_even((width - video_w) / 2), video_y=top + _even((room - video_h) / 2))  # fmt: skip


def _round_logo(logo: Path, diameter: int) -> Image.Image:
    image = Image.open(logo).convert("RGBA").resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    image.putalpha(mask)
    return image


def build_frame(banner: Path, logo: Path | None, layout: BrandLayout, out: Path) -> Path:
    """Draw the bands into a transparent `layout.width` x `layout.height` PNG (atomic write)."""
    width, height = layout.width, layout.height
    frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    source = Image.open(banner).convert("RGBA")

    frame.paste(source.resize((width, layout.bottom), Image.LANCZOS), (0, height - layout.bottom))
    ImageDraw.Draw(frame).rectangle((0, 0, width - 1, layout.top - 1), fill=BG_COLOR + (255,))
    strip_src = source.crop((0, 0, source.width, round(source.height * STRIP_RATIO)))
    strip_h = max(2, round(strip_src.height * width / source.width))
    frame.paste(strip_src.resize((width, strip_h), Image.LANCZOS), (0, layout.top - strip_h))
    if logo is not None:
        margin = max(1, round(layout.top * LOGO_MARGIN_RATIO))
        diameter = layout.top - 2 * margin
        round_logo = _round_logo(logo, diameter)
        # a round badge over the film strip, centred on the whole band
        frame.alpha_composite(round_logo, (width - round(width * LOGO_RIGHT_RATIO) - diameter, margin))

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.tmp.png")
    frame.save(tmp)
    tmp.replace(out)
    return out


def brand_signature(banner: str | None, logo: str | None) -> str | None:
    """Fingerprint part for stages that draw the branding; None = no branding."""
    if not banner:
        return None
    files = [config.file_signature(Path(p).expanduser()) for p in (banner, logo) if p]
    return "brand:" + ":".join([*files, BRAND_VERSION])
