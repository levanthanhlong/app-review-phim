"""Rules of the `inspect` stage (pure functions, no I/O): what in the source must not reach the recap.

Input: the text OCR found in one frame per second, and a map of pixels that never change
across the film. Output:
- overlays: a channel watermark (the same text in the same place in most frames; its region is
  grown to the whole static logo) or burned-in subtitles (text in one place, always different)
- flags: frames with ads, sponsors, credits, behind-the-scenes or disclaimers (keywords and
  domains outside the overlays), or with title cards at the very start or end
- skip ranges: flags widened into time ranges (an end card or behind-the-scenes near the end
  skips to the end; cards at the start skip from the start), then the beats that touch them
"""

from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from recap.models import Beat

MIN_CONF = 0.5  # real overlaid text reads at ~1.0; blurry background signs at ~0.3
MIN_TEXT_LEN = 3
GRID_X, GRID_Y = 16, 9
PERSIST_SHARE = 0.3  # an overlay has text in at least this share of the frames
STABLE_SHARE = 0.3  # ...and for a watermark, its most common text is this share of those frames
STABLE_TEXT_SHARE = 0.1  # texts making up a watermark appear in at least this share of all frames
REFINE_PAD = 0.06  # static pixels this close to a watermark's text belong to its logo
REGION_PAD = 0.01
CORNER = 0.3  # corner windows searched for logos without text
MIN_LOGO_SHARE = 0.002  # of the frame's pixels: smaller static specks are noise
INTRO_SEC, OUTRO_SEC = 90.0, 180.0  # title cards are looked for this close to the start / end
HEAD_SHARE, TAIL_SHARE = 0.15, 0.25  # flags this close to the start / end skip from the start / to the end
MIN_TITLE_BOXES = 2
MIN_FLAG_RUN = 2  # a flag counts only with this many flags (itself included) within...
MAX_FLAG_GAP_SEC = 3.0  # ...this many seconds of it

Kind = Literal["ad", "credit", "bts", "disclaimer", "title"]
EDGE_KINDS = ("ad", "credit", "bts", "disclaimer", "title")

KEYWORDS: dict[str, tuple[str, ...]] = {
    "ad": ("tài trợ", "quảng cáo", "khuyến mãi", "giảm giá", "cá cược", "nhà cái", "casino", "đặt cược",
           "nổ hũ", "tải app", "hotline", "đăng ký kênh", "theo dõi kênh", "subscribe", "sponsor"),
    "credit": ("đạo diễn", "biên kịch", "kịch bản", "nhà sản xuất", "sản xuất bởi", "diễn viên", "quay phim",
               "dựng phim", "director", "producer", "produced by", "written by", "screenplay"),
    "bts": ("behind the scenes", "hậu trường", "making of", "bloopers"),
    "disclaimer": ("miễn trừ", "tuyên bố", "hư cấu", "trùng hợp ngẫu nhiên", "disclaimer", "fictional",
                   "coincidental"),
}  # fmt: skip
_PATTERNS = {kind: re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b") for kind, words in KEYWORDS.items()}
_EPISODE = re.compile(r"\b(tập|episode|ep)\s*\d+\b")
_DOMAIN = re.compile(r"\b[\w-]+\.(com|net|vn|tv|me|io|xyz|club|org|info|top|bet|vip|app|live)\b|@[\w-]+\.\w+")


@dataclass(frozen=True)
class TextBox:
    """One piece of text OCR found; position normalized to the frame, origin top-left."""

    text: str
    conf: float
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


@dataclass(frozen=True)
class FrameText:
    time: float
    boxes: tuple[TextBox, ...]


@dataclass(frozen=True)
class Region:
    x: float
    y: float
    w: float
    h: float

    def contains(self, point: tuple[float, float]) -> bool:
        px, py = point
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h

    def overlaps(self, other: Region) -> bool:
        return self.x < other.x + other.w and other.x < self.x + self.w and self.y < other.y + other.h and other.y < self.y + self.h

    def union(self, other: Region) -> Region:
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        x1, y1 = max(self.x + self.w, other.x + other.w), max(self.y + self.h, other.y + other.h)
        return Region(x0, y0, x1 - x0, y1 - y0)

    def padded(self, pad: float) -> Region:
        """Grown by `pad` on every side, kept inside the frame."""
        x0, y0 = max(0.0, self.x - pad), max(0.0, self.y - pad)
        x1, y1 = min(1.0, self.x + self.w + pad), min(1.0, self.y + self.h + pad)
        return Region(round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4))


@dataclass(frozen=True)
class Overlay:
    kind: Literal["watermark", "subtitles"]
    region: Region
    text: str  # what it says (watermark) or a sample (subtitles)


@dataclass(frozen=True)
class Flag:
    time: float
    kind: Kind
    text: str
    region: Region | None = None  # where the text is (None for a whole title card)


@dataclass(frozen=True)
class SkipRange:
    start: float
    end: float
    reason: str


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _confident(boxes: Iterable[TextBox]) -> list[TextBox]:
    return [b for b in boxes if b.conf >= MIN_CONF and len(b.text.strip()) >= MIN_TEXT_LEN]


def _box_region(b: TextBox) -> Region:
    return Region(b.x, b.y, b.w, b.h)


# ---- overlays ----------------------------------------------------------------------------------


def _cells(b: TextBox) -> set[tuple[int, int]]:
    xs = range(int(b.x * GRID_X), min(GRID_X - 1, int((b.x + b.w) * GRID_X)) + 1)
    ys = range(int(b.y * GRID_Y), min(GRID_Y - 1, int((b.y + b.h) * GRID_Y)) + 1)
    return {(cx, cy) for cx in xs for cy in ys}


def _components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """4-connected groups of grid cells."""
    left, groups = set(cells), []
    while left:
        start = left.pop()
        group, queue = {start}, deque([start])
        while queue:
            cx, cy = queue.popleft()
            for n in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if n in left:
                    left.remove(n)
                    group.add(n)
                    queue.append(n)
        groups.append(group)
    return groups


def _overlay(group: set[tuple[int, int]], frames: Sequence[FrameText]) -> Overlay:
    in_group = [[b for b in _confident(f.boxes) if _cells(b) & group] for f in frames]
    with_text = sum(1 for boxes in in_group if boxes)
    texts = Counter(_norm(b.text) for boxes in in_group for b in {_norm(x.text): x for x in boxes}.values())
    top_text, top_count = texts.most_common(1)[0]
    if top_count / max(1, with_text) >= STABLE_SHARE:
        stable = {t for t, n in texts.items() if n >= STABLE_TEXT_SHARE * len(frames)}
        regions = [_box_region(b) for boxes in in_group for b in boxes if _norm(b.text) in stable]
        region = regions[0]
        for other in regions[1:]:
            region = region.union(other)
        return Overlay("watermark", region, " | ".join(sorted(stable)))
    xs, ys = [c[0] for c in group], [c[1] for c in group]
    region = Region(min(xs) / GRID_X, min(ys) / GRID_Y, (max(xs) + 1 - min(xs)) / GRID_X, (max(ys) + 1 - min(ys)) / GRID_Y)
    return Overlay("subtitles", region, top_text)


def find_overlays(frames: Sequence[FrameText]) -> list[Overlay]:
    """Watermarks and burned-in subtitles: text in the same place in at least PERSIST_SHARE of the frames."""
    if not frames:
        return []
    counts: Counter[tuple[int, int]] = Counter()
    for frame in frames:
        counts.update(set().union(*(_cells(b) for b in _confident(frame.boxes))) if frame.boxes else set())
    persistent = {cell for cell, n in counts.items() if n >= PERSIST_SHARE * len(frames)}
    return [_overlay(group, frames) for group in _components(persistent)]


# ---- static pixels -----------------------------------------------------------------------------

STATIC_TOLERANCE = 12.0  # grey levels a static pixel may vary by (compression noise)
STATIC_SHARE = 0.7  # ...in at least this share of the frames: logos missing from the intro still count
MIN_STATIC_LEVEL = 24.0  # darker static pixels are black bars, not logos


def static_mask(stack: np.ndarray) -> np.ndarray:
    """Pixels of a (frames, height, width) grey stack that keep their usual value in most frames."""
    median = np.median(stack, axis=0)
    steady = (np.abs(stack - median) < STATIC_TOLERANCE).mean(axis=0) >= STATIC_SHARE
    return steady & (median > MIN_STATIC_LEVEL)



def _blob_bbox(static: np.ndarray, seeds: Iterable[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1) of the 8-connected static pixels reachable from `seeds`."""
    height, width = static.shape
    seen = set(seeds)
    queue = deque(seen)
    if not seen:
        return None
    while queue:
        y, x = queue.popleft()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                n = (y + dy, x + dx)
                if 0 <= n[0] < height and 0 <= n[1] < width and n not in seen and static[n]:
                    seen.add(n)
                    queue.append(n)
    ys, xs = zip(*seen)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _to_region(bbox: tuple[int, int, int, int], static: np.ndarray) -> Region:
    height, width = static.shape
    x0, y0, x1, y1 = bbox
    return Region(x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height)


def refine_region(region: Region, static: np.ndarray) -> Region:
    """Grow a watermark's text region to the static logo around it (the OCR sees only its text)."""
    height, width = static.shape
    window = region.padded(REFINE_PAD)
    y0, y1 = int(window.y * height), int(np.ceil((window.y + window.h) * height))
    x0, x1 = int(window.x * width), int(np.ceil((window.x + window.w) * width))
    seeds = [(int(y), int(x)) for y, x in zip(*np.nonzero(static[y0:y1, x0:x1]))]
    bbox = _blob_bbox(static, [(y + y0, x + x0) for y, x in seeds])
    grown = region if bbox is None else region.union(_to_region(bbox, static))
    return grown.padded(REGION_PAD)


def static_logos(static: np.ndarray) -> list[Region]:
    """Logos without text: a large enough patch of static pixels in a corner of the frame."""
    height, width = static.shape
    cw, ch = int(width * CORNER), int(height * CORNER)
    logos = []
    for x0, y0 in ((0, 0), (width - cw, 0), (0, height - ch), (width - cw, height - ch)):
        window = static[y0 : y0 + ch, x0 : x0 + cw]
        if window.sum() < MIN_LOGO_SHARE * static.size:
            continue
        blob = _largest_blob(window)
        if len(blob) < MIN_LOGO_SHARE * static.size:
            continue
        ys, xs = zip(*blob)
        bbox = (min(xs) + x0, min(ys) + y0, max(xs) + 1 + x0, max(ys) + 1 + y0)
        logos.append(_to_region(bbox, static).padded(REGION_PAD))
    return logos


def _largest_blob(window: np.ndarray) -> set[tuple[int, int]]:
    """The largest 8-connected patch of static pixels (stray still pixels elsewhere are not the logo)."""
    left = {(int(y), int(x)) for y, x in zip(*np.nonzero(window))}
    best: set[tuple[int, int]] = set()
    while left:
        seed = left.pop()
        blob, queue = {seed}, deque([seed])
        while queue:
            y, x = queue.popleft()
            for n in ((y + dy, x + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                if n in left:
                    left.discard(n)
                    blob.add(n)
                    queue.append(n)
        best = max(best, blob, key=len)
    return best


# ---- flags -------------------------------------------------------------------------------------


def _kind(text: str) -> Kind | None:
    normalized = _norm(text)
    for kind, pattern in _PATTERNS.items():
        if pattern.search(normalized):
            return kind  # type: ignore[return-value]
    if _EPISODE.search(normalized):
        return "credit"
    if _DOMAIN.search(normalized):
        return "ad"
    return None


def flag_frames(frames: Sequence[FrameText], overlays: Sequence[Overlay], duration: float) -> list[Flag]:
    """Frames showing ads, sponsors, credits, behind-the-scenes or disclaimers (text outside the
    overlays), and frames with title cards close to the start or end of the film."""
    flags = []
    for frame in frames:
        boxes = [b for b in _confident(frame.boxes) if not any(o.region.contains(b.center) for o in overlays)]
        found = [Flag(frame.time, k, b.text, _box_region(b)) for b in boxes if (k := _kind(b.text))]
        if found:
            flags.extend(found)
        elif len(boxes) >= MIN_TITLE_BOXES and (frame.time < INTRO_SEC or frame.time > duration - OUTRO_SEC):
            flags.append(Flag(frame.time, "title", " / ".join(b.text for b in boxes)))
    return flags


# ---- ranges and beats --------------------------------------------------------------------------

REASONS = {"ad": "quảng cáo", "credit": "credit", "bts": "hậu trường", "disclaimer": "tuyên bố", "title": "thẻ tiêu đề"}


def _reason(flag: Flag) -> str:
    return f"{REASONS[flag.kind]}: {flag.text}"


def _merge(ranges: list[SkipRange], gap: float) -> list[SkipRange]:
    merged: list[SkipRange] = []
    for r in sorted(ranges, key=lambda r: r.start):
        if merged and r.start <= merged[-1].end + gap:
            merged[-1] = replace(merged[-1], end=max(merged[-1].end, r.end))
        else:
            merged.append(r)
    return merged


def _corroborated(flags: Sequence[Flag], interval: float) -> list[Flag]:
    """Flags with another flag close by: cards stay on screen for seconds, while text read in a
    single frame is usually a sign, a phone screen or an OCR mistake."""
    gap = max(MAX_FLAG_GAP_SEC, interval * 2)
    times = sorted(f.time for f in flags)
    return [f for f in flags if sum(1 for t in times if abs(t - f.time) <= gap) >= MIN_FLAG_RUN]


def skip_ranges(flags: Sequence[Flag], duration: float, interval: float = 1.0) -> list[SkipRange]:
    """Time ranges to leave out. Only flags seen in consecutive samples count. Every flag covers its
    sampling interval; flags near the start skip everything before them, flags near the end
    everything after them (title, credits, outtakes)."""
    half = interval / 2
    flags = _corroborated(flags, interval)
    head = [f for f in flags if f.time <= duration * HEAD_SHARE and f.kind in EDGE_KINDS]
    tail = [f for f in flags if f.time >= duration * (1 - TAIL_SHARE) and f.kind in EDGE_KINDS]
    ranges = [SkipRange(max(0.0, f.time - half), min(duration, f.time + half), _reason(f)) for f in flags]
    if head:
        ranges.append(SkipRange(0.0, min(duration, head[-1].time + half), _reason(head[0])))
    if tail:
        ranges.append(SkipRange(max(0.0, tail[0].time - half), duration, _reason(tail[0])))
    return _merge(ranges, gap=0.0)


def beats_to_skip(beats: Sequence[Beat], ranges: Sequence[SkipRange]) -> dict[str, str]:
    """Beat id -> reason, for every beat that overlaps a skip range."""
    skipped = {}
    for beat in beats:
        hit = next((r for r in ranges if beat.start < r.end and beat.end > r.start), None)
        if hit:
            skipped[beat.id] = hit.reason
    return skipped
