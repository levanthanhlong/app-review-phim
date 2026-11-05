"""Stage `inspect`: what in the source must not reach the recap (spec: expert review, item 1).

1. One frame per second of the proxy is read with OCR (cached by proxy, so new beats only
   re-apply the rules), and ~150 of them form a map of pixels that never change.
2. `inspect_rules` turns that into watermarks (blurred when rendering), burned-in subtitles
   (left alone), and time ranges with ads, sponsors, credits, outtakes or title cards, whose beats
   are skipped (the user can keep a beat in the Scene Browser).
3. Black bars in the source are found with ffmpeg's cropdetect (cropped when rendering).

Writes work/inspect.json; the sampled frames are deleted afterwards.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from recap import config, media
from recap.ad_blurs import ad_blurs, outside, split_ads
from recap.inspect_rules import (
    FrameText,
    Overlay,
    Region,
    TextBox,
    beats_to_skip,
    black_bars,
    find_overlays,
    flag_frames,
    refine_region,
    skip_ranges,
    static_logos,
    static_mask,
)
from recap.models import SCENES_ADAPTER, Project
from recap.ocr import Ocr, OcrError, recognize
from recap.runner import StageContext, StageFailure

INSPECT = "work/inspect.json"
WORK_DIR = "work/inspect"
FRAMES_DIR = f"{WORK_DIR}/frames"
OCR_CACHE = f"{WORK_DIR}/ocr.json"
PROXY = "work/proxy_480p.mp4"
SCENES = "work/scenes.json"
SAMPLE_FPS = 1.0
STATIC_FRAMES = 150  # frames used for the map of static pixels
STATIC_SIZE = (240, 135)
CROP_SAMPLE_FPS = 0.5
MIN_CROP_SHARE = 0.95  # a crop keeping more of the frame than this is no black bars
MIN_KEPT_SHARE = 0.5  # a crop keeping less than this is a dark scene, not black bars
MAX_BAR_DIFF = 0.05  # bars on opposite sides differ by at most this share of the frame
CROPDETECT_TIMEOUT_SEC = 600
CACHE_EVERY = 100  # frames between saves of the OCR cache, so a cancel or crash can resume
OCR_VERSION = "1"  # bump when what `recognize` returns changes
RULES_VERSION = "6"  # bump when `inspect_rules` decides differently
OK_HEIGHT, ACCEPTABLE_HEIGHT = 1080, 720
PROGRESS_EVERY = 20

_CROP = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def parse_cropdetect(stderr: str, width: int, height: int) -> Region | None:
    """The black-bar-free area cropdetect reports in most samples, or None when there is nothing worth
    cropping or the answer does not look like black bars (no majority, too small, lopsided)."""
    crops = Counter(_CROP.findall(stderr))
    if not crops:
        return None
    best, count = crops.most_common(1)[0]
    w, h, x, y = map(int, best)
    if count * 2 < sum(crops.values()) or w * h >= MIN_CROP_SHARE * width * height:
        return None
    lopsided = abs(x - (width - x - w)) > MAX_BAR_DIFF * width or abs(y - (height - y - h)) > MAX_BAR_DIFF * height
    if w * h < MIN_KEPT_SHARE * width * height or lopsided:
        return None
    return Region(round(x / width, 4), round(y / height, 4), round(w / width, 4), round(h / height, 4))


def _quality(project: Project) -> str:
    height = project.media.height if project.media and project.media.height else 0
    return "ok" if height >= OK_HEIGHT else "acceptable" if height >= ACCEPTABLE_HEIGHT else "low"


def _region_dict(region: Region, text: str) -> dict[str, Any]:
    return {**asdict(region), "text": text}


class InspectStage:
    name = "inspect"

    def __init__(self, ocr: Ocr = recognize) -> None:
        self._ocr = ocr

    def outputs(self, project: Project) -> list[str]:
        return [INSPECT]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        sigs = [config.file_signature(project_dir / f) for f in (PROXY, SCENES)]
        return "|".join([*sigs, OCR_VERSION, RULES_VERSION])

    def run(self, ctx: StageContext) -> Project:
        proxy = ctx.path(PROXY)
        if not proxy.is_file():
            raise StageFailure("input_missing", "Thiếu proxy_480p.mp4, hãy chạy lại stage ingest")
        duration = ctx.project.media.duration if ctx.project.media else 0.0
        frames_dir = ctx.path(FRAMES_DIR)
        try:
            frames, stack = self._read_frames(ctx, proxy, frames_dir, duration)
        finally:
            shutil.rmtree(frames_dir, ignore_errors=True)
        report = self._report(ctx, frames, stack, duration, proxy)
        config.write_json_atomic(ctx.path(INSPECT), report)
        self._log_summary(ctx, report)
        ctx.emitter.artifact(self.name, INSPECT)
        return ctx.project

    # ---- reading the source ------------------------------------------------------------------

    def _read_frames(
        self, ctx: StageContext, proxy: Path, frames_dir: Path, duration: float
    ) -> tuple[list[FrameText], np.ndarray]:
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir(parents=True)
        try:
            media.run_ffmpeg(["-i", str(proxy), "-vf", f"fps={SAMPLE_FPS:g}", "-q:v", "4", str(frames_dir / "%05d.jpg")],
                             total_sec=duration, on_progress=lambda _: None, log=ctx.log)  # fmt: skip
        except media.MediaError as exc:
            raise StageFailure("inspect_failed", f"Không lấy được khung hình: {exc}", retryable=True) from exc
        files = sorted(frames_dir.glob("*.jpg"))
        if not files:
            raise StageFailure("inspect_failed", "Không lấy được khung hình nào từ proxy")
        return self._ocr_frames(ctx, files), self._gray_stack(files)

    def _ocr_frames(self, ctx: StageContext, files: list[Path]) -> list[FrameText]:
        """Text in every frame. Results are cached as they come (a cancelled run resumes); a frame
        Vision cannot read counts as no text, and is not cached so the next run tries it again."""
        cache = ctx.path(OCR_CACHE)
        key = "|".join([config.file_signature(ctx.path(PROXY)), f"{SAMPLE_FPS:g}", OCR_VERSION])
        frames = [] if ctx.force else _load_ocr_cache(cache, key)[: len(files)]
        if frames:
            ctx.emitter.log("info", f"Dùng lại kết quả đọc chữ của {len(frames)}/{len(files)} khung hình")
        readable = len(frames)  # frames before the first failure: the part worth caching
        failures: list[str] = []
        try:
            for n in range(len(frames), len(files)):
                try:
                    boxes = self._ocr(files[n])
                except OcrError as exc:
                    failures.append(str(exc))
                    boxes = []
                frames.append(FrameText(round(n / SAMPLE_FPS, 3), tuple(boxes)))
                readable += 0 if failures else 1
                if (n + 1) % PROGRESS_EVERY == 0 or n + 1 == len(files):
                    ctx.emitter.progress(self.name, n + 1, len(files), "Đọc chữ trong hình")
                if (n + 1) % CACHE_EVERY == 0:
                    _save_ocr_cache(cache, key, frames[:readable])
        finally:
            _save_ocr_cache(cache, key, frames[:readable])
        if failures:
            ctx.log.write("\n".join(failures[:20]) + "\n")
            ctx.emitter.log("warning", f"Không đọc chữ được ở {len(failures)}/{len(files)} khung hình "
                                       f"({failures[0]}): những khung đó coi như không có chữ")  # fmt: skip
        return frames

    @staticmethod
    def _gray_stack(files: list[Path]) -> np.ndarray:
        """Up to ~150 small grey frames: for static pixels (logos) and black bars."""
        step = max(1, len(files) // STATIC_FRAMES)
        return np.stack([_gray(f) for f in files[::step]])

    # ---- decisions ---------------------------------------------------------------------------

    def _report(self, ctx: StageContext, frames: list[FrameText], stack: np.ndarray, duration: float, proxy: Path) -> dict[str, Any]:
        overlays = find_overlays(frames)
        watermarks = _watermarks(overlays, static_mask(stack))
        overlay_ads, flags = split_ads(flag_frames(frames, overlays))
        ranges = skip_ranges(flags, duration, interval=1 / SAMPLE_FPS)
        ads = [{"start": a.start, "end": a.end, **_region_dict(a.region, a.text)} for a in outside(ad_blurs(overlay_ads, 1 / SAMPLE_FPS), ranges)]
        beats = SCENES_ADAPTER.validate_json(ctx.path(SCENES).read_bytes()) if ctx.path(SCENES).is_file() else []
        info = ctx.project.media
        return {
            "source": {"width": info.width if info else None, "height": info.height if info else None,
                       "quality": _quality(ctx.project)},
            "watermarks": watermarks,
            "subtitles": [_region_dict(o.region, o.text) for o in overlays if o.kind == "subtitles"],
            "letterbox": _letterbox(proxy, ctx, stack),
            "ad_blurs": ads,
            "skip_ranges": [asdict(r) for r in ranges],
            "skip_beats": beats_to_skip(beats, ranges),
        }  # fmt: skip

    def _log_summary(self, ctx: StageContext, report: dict[str, Any]) -> None:
        for wm in report["watermarks"]:
            ctx.emitter.log("info", f"Watermark sẽ được làm mờ: {wm['text'] or 'logo không chữ'}")
        for ad in report["ad_blurs"]:
            ctx.emitter.log("info", f"Quảng cáo sẽ được làm mờ {ad['start']:.0f}s–{ad['end']:.0f}s: {ad['text'][:60]}")
        for r in report["skip_ranges"]:
            ctx.emitter.log("info", f"Bỏ {r['start']:.0f}s–{r['end']:.0f}s ({r['reason'][:80]})")
        ctx.emitter.log("info", f"Bỏ {len(report['skip_beats'])} cảnh; viền đen: {'có' if report['letterbox'] else 'không'}")
        if report["source"]["quality"] == "low":
            ctx.emitter.log("warning", f"Nguồn chỉ cao {report['source']['height']}px (dưới 720p): nên tìm bản tốt hơn")


def _watermarks(overlays: list[Overlay], static: np.ndarray) -> list[dict[str, Any]]:
    """OCR'd watermarks grown to their logo, plus static corner logos that have no text."""
    found = [(refine_region(o.region, static), o.text) for o in overlays if o.kind == "watermark"]
    for logo in static_logos(static):
        if not any(logo.overlaps(region) for region, _ in found):
            found.append((logo, ""))
    return [_region_dict(region, text) for region, text in found]


def _letterbox(proxy: Path, ctx: StageContext, stack: np.ndarray) -> dict[str, float] | None:
    """Black bars by ffmpeg's cropdetect, else by the frames' dark rows and columns (which still finds
    bars with a translator's credit or subtitles written in them)."""
    region = _cropdetect(proxy, ctx) or black_bars(stack)
    return asdict(region) if region else None


def _cropdetect(proxy: Path, ctx: StageContext) -> Region | None:
    info = media.probe(proxy)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(proxy),
           "-vf", f"fps={CROP_SAMPLE_FPS:g},cropdetect=limit=24:round=2:reset=1", "-f", "null", "-"]  # fmt: skip
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=CROPDETECT_TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as exc:
        ctx.emitter.log("warning", f"Không dò được viền đen của nguồn ({exc}), bỏ qua bước này")
        return None
    ctx.log.write(result.stderr[-4000:])
    if result.returncode != 0 or not info.width or not info.height:
        ctx.emitter.log("warning", "Không dò được viền đen của nguồn, bỏ qua bước này")
        return None
    return parse_cropdetect(result.stderr, info.width, info.height)


def _gray(file: Path) -> np.ndarray:
    try:
        with Image.open(file) as image:
            return np.asarray(image.convert("L").resize(STATIC_SIZE), dtype=np.float32)
    except OSError as exc:
        raise StageFailure("inspect_failed", f"Khung hình hỏng {file.name}: {exc}", retryable=True) from exc


def _save_ocr_cache(path: Path, key: str, frames: list[FrameText]) -> None:
    config.write_json_atomic(path, {"key": key, "frames": [asdict(f) for f in frames]})


def _load_ocr_cache(path: Path, key: str) -> list[FrameText]:
    """The frames read so far for this proxy (from the start, in order); none if missing or damaged."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("key") != key:
            return []
        return [FrameText(f["time"], tuple(TextBox(**b) for b in f["boxes"])) for f in data["frames"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []
