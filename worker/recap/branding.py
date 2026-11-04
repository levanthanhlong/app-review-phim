"""Channel branding: a thin film-strip band above the film and the channel banner below it.

The banner image is scaled to the bottom band's height (keeping its aspect: avatar, name and
"follow" button shrink together) on a strip of the banner's background colour; the film fills
the rest of the frame (cropping a little rather than leaving black bars). With `overlay`, the
film fills the whole frame and the banner lies over its bottom edge, its background see-through.
A small round logo can sit in a corner of the film.

Sizes, opacity and placement come from `brand.json` next to the banner image (`BrandStyle`).
Everything is drawn once per frame size into a transparent PNG that the renderer lays over
each clip; the film is scaled into the box (`BrandLayout`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from recap import config

BG_COLOR = (26, 20, 35)  # background of the "Mắm review phim" banner
STRIP_RATIO = 40 / 260  # height of the film strip along the banner's top edge, of the banner height
LOGO_MARGIN_RATIO = 0.02  # logo's distance from the film's edges, of the frame height
BG_KEY_DISTANCE = 40  # banner pixels this close to BG_COLOR count as background (see-through in overlay)
STYLE_FILE = "brand.json"
BRAND_VERSION = "3"  # bump when the frame drawing changes, to re-render cached clips

Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]


class BrandingError(Exception):
    pass


class BrandStyle(BaseModel):
    """Contents of brand.json (every key optional)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    top_ratio: float = Field(default=0.02, ge=0, le=0.02)  # film-strip band above the film; 0 = none
    bottom_ratio: float = Field(default=0.08, ge=0.03, le=0.08)  # banner band
    banner_align: Literal["left", "center", "right"] = "center"
    overlay: bool = False  # banner over the film's bottom edge instead of its own band
    overlay_opacity: float = Field(default=0.6, ge=0, le=1)  # of the banner background, in overlay
    video_fit: Literal["fill", "fit"] = "fill"  # fill = crop the overflow, fit = whole picture (bars)
    logo_corner: Corner | None = "top-right"  # None = no logo over the film
    logo_ratio: float = Field(default=0.07, ge=0.03, le=0.15)  # logo diameter, of the frame height


@dataclass(frozen=True)
class BrandLayout:
    width: int
    height: int
    top: int  # band above the film
    bottom: int  # banner band
    video_w: int  # the box the film is scaled into
    video_h: int
    video_x: int
    video_y: int
    banner_w: int  # the banner image, scaled to the band's height
    banner_x: int
    fill: bool  # film covers the box (cropped) instead of fitting inside it

    @property
    def film_area(self) -> float:
        """Share of the frame where the film shows (an overlaid banner covers its bottom)."""
        visible_h = self.video_h - (self.bottom if self.video_y + self.video_h > self.height - self.bottom else 0)
        return self.video_w * visible_h / (self.width * self.height)


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


def load_style(banner: Path) -> BrandStyle:
    """brand.json next to the banner image; none = the defaults."""
    path = banner.parent / STYLE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return BrandStyle()
    try:
        return BrandStyle.model_validate(json.loads(text))
    except json.JSONDecodeError as exc:
        raise BrandingError(f"{STYLE_FILE} không phải JSON hợp lệ: {exc}") from exc
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise BrandingError(f"{STYLE_FILE}: {problems}") from exc


def brand_layout(banner_size: tuple[int, int], width: int, height: int, style: BrandStyle | None = None) -> BrandLayout:
    style = style or BrandStyle()
    banner_w, banner_h = banner_size
    top = _even(height * style.top_ratio)
    bottom = _even(height * style.bottom_ratio)
    scaled_w = min(width, _even(bottom * banner_w / banner_h))
    banner_x = {"left": 0, "center": _even((width - scaled_w) / 2), "right": width - scaled_w}[style.banner_align]
    if style.overlay:
        box = (0, 0, width, height)
    else:
        box = (0, top, width, height - top - bottom)
    return BrandLayout(width, height, top, bottom, video_w=box[2], video_h=box[3], video_x=box[0], video_y=box[1],
                       banner_w=scaled_w, banner_x=banner_x, fill=style.video_fit == "fill")  # fmt: skip


def _round_logo(logo: Path, diameter: int) -> Image.Image:
    image = Image.open(logo).convert("RGBA").resize((diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    image.putalpha(mask)
    return image


def _see_through(banner: Image.Image, opacity: float) -> Image.Image:
    """The banner with its background colour made `opacity` opaque (text and images stay solid)."""
    pixels = np.array(banner)
    distance = np.abs(pixels[..., :3].astype(int) - np.array(BG_COLOR)).sum(axis=-1)
    pixels[..., 3] = np.where(distance <= BG_KEY_DISTANCE, round(255 * opacity), pixels[..., 3])
    return Image.fromarray(pixels, "RGBA")


def _logo_position(layout: BrandLayout, corner: Corner, diameter: int) -> tuple[int, int]:
    margin = round(layout.height * LOGO_MARGIN_RATIO)
    film_bottom = min(layout.video_y + layout.video_h, layout.height - layout.bottom)
    x = layout.video_x + margin if corner.endswith("left") else layout.video_x + layout.video_w - margin - diameter
    y = layout.video_y + margin if corner.startswith("top") else film_bottom - margin - diameter
    return x, y


def build_frame(banner: Path, logo: Path | None, layout: BrandLayout, style: BrandStyle, out: Path) -> Path:
    """Draw the bands into a transparent `layout.width` x `layout.height` PNG (atomic write)."""
    width, height = layout.width, layout.height
    frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    source = Image.open(banner).convert("RGBA")

    band_alpha = round(255 * style.overlay_opacity) if style.overlay else 255
    ImageDraw.Draw(frame).rectangle((0, height - layout.bottom, width - 1, height - 1), fill=BG_COLOR + (band_alpha,))
    scaled = source.resize((layout.banner_w, layout.bottom), Image.LANCZOS)
    if style.overlay:
        scaled = _see_through(scaled, style.overlay_opacity)
        frame.paste(scaled, (layout.banner_x, height - layout.bottom))  # replace the band's pixels, not blend
    else:
        frame.alpha_composite(scaled, (layout.banner_x, height - layout.bottom))
    if layout.top > 0:
        strip = source.crop((0, 0, source.width, round(source.height * STRIP_RATIO)))
        frame.paste(strip.resize((width, layout.top), Image.LANCZOS), (0, 0))
    if logo is not None and style.logo_corner is not None:
        diameter = _even(height * style.logo_ratio)
        frame.alpha_composite(_round_logo(logo, diameter), _logo_position(layout, style.logo_corner, diameter))

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
    style_file = Path(banner).expanduser().parent / STYLE_FILE
    style = hashlib.sha256(style_file.read_bytes()).hexdigest()[:12] if style_file.is_file() else "default"
    return "brand:" + ":".join([*files, style, BRAND_VERSION])
