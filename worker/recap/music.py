"""Background music mixed under the whole recap when the clips are joined.

The bed is one or more cues played in turn: a single library track spanning the recap, or the
AI cues of work/music/plan.json (the chosen option of each), crossfaded at their boundaries.
Levels: `music_bed_volume` with nobody talking, `music_volume` under narration, and
CLIP_FACTOR of that while an `original` line keeps the film's own sound. A track shorter than
its cue is repeated with crossfades; a very short loop uses `-stream_loop` instead (too many
inputs otherwise).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from recap import config, media
from recap.fit import TimelineEntry
from recap.models import Settings

MUSIC_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".aif", ".aiff")
CLIP_FACTOR = 0.25  # music level under an original clip, relative to music_volume (-12 dB)
DUCK_RAMP_SEC = 0.5
NARRATION_RAMP_SEC = 0.3
FADE_IN_SEC = 2.0
FADE_OUT_SEC = 3.0
LOOP_XFADE_SEC = 3.0
CUE_XFADE_SEC = 2.0  # overlap between two AI cues
MAX_LOOP_COPIES = 40
AUDIO_RATE = 48000
MS_PER_SEC = 1000
EPS = 1e-6
PLAN = "work/music/plan.json"


class MusicError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MusicCue:
    path: Path
    track_sec: float
    out_start: float
    out_end: float  # math.inf = until the end of the recap


@dataclass(frozen=True)
class MusicBed:
    cues: tuple[MusicCue, ...]
    bed_volume: float  # nobody talking
    voice_volume: float  # under narration


def _num(value: float) -> str:
    return f"{value:.6g}"


def check_music_file(text: str) -> Path:
    path = Path(text).expanduser()
    if not path.is_file():
        raise MusicError("music_missing", f"Không tìm thấy file nhạc {path}")
    if path.suffix.lower() not in MUSIC_EXTENSIONS:
        formats = ", ".join(e.lstrip(".") for e in MUSIC_EXTENSIONS)
        raise MusicError("music_invalid", f"{path.name} không phải định dạng nhạc hỗ trợ ({formats})")
    return path


def music_signature(settings: Settings, project_dir: Path) -> str | None:
    """Fingerprint part for stages that mix the music; None keeps old fingerprints unchanged."""
    levels = f"{settings.music_volume}:{settings.music_bed_volume}"
    if settings.music_source == "ai":
        return f"music:ai:{config.file_signature(project_dir / PLAN)}:{levels}"
    if not settings.music_file:
        return None
    return f"music:{config.file_signature(Path(settings.music_file).expanduser())}:{levels}"


def _track_seconds(path: Path) -> float:
    try:
        info = media.probe(path)
    except media.MediaError as exc:
        raise MusicError("music_invalid", f"Không đọc được file nhạc {path.name}: {exc}") from exc
    if not info.audio_tracks:
        raise MusicError("music_invalid", f"{path.name} không có âm thanh")
    return info.duration


def _ai_cues(project_dir: Path) -> tuple[MusicCue, ...]:
    try:
        plan = json.loads((project_dir / PLAN).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise MusicError("music_missing", "Chưa có nhạc AI, hãy chạy stage music") from exc
    cues = []
    for cue in plan.get("cues", []):
        options = cue.get("options") or []
        chosen = cue.get("chosen", 0)
        if not 0 <= chosen < len(options):
            raise MusicError("music_missing", f"{cue.get('id')}: chưa tạo nhạc, hãy chạy stage music")
        path = project_dir / "work" / options[chosen]["file"]
        if not path.is_file():
            raise MusicError("music_missing", f"Thiếu file nhạc {path.name}, hãy chạy lại stage music")
        cues.append(MusicCue(path, _track_seconds(path), float(cue["out_start"]), float(cue["out_end"])))
    if not cues:
        raise MusicError("music_missing", "plan.json không có phần nhạc nào, hãy chạy lại stage music")
    return tuple(cues)


def music_bed(settings: Settings, project_dir: Path) -> MusicBed | None:
    """The project's background music (None = no music); checked before any rendering starts."""
    if settings.music_source == "ai":
        cues = _ai_cues(project_dir)
    elif settings.music_file:
        path = check_music_file(settings.music_file)
        cues = (MusicCue(path, _track_seconds(path), 0.0, math.inf),)
    else:
        return None
    return MusicBed(cues, settings.music_bed_volume, settings.music_volume)


# ---- placement --------------------------------------------------------------------------


def _intervals(timeline: Sequence[TimelineEntry], kind: str) -> tuple[tuple[float, float], ...]:
    spans: list[tuple[float, float]] = []
    for entry in timeline:
        if entry.type != kind:
            continue
        if spans and abs(spans[-1][1] - entry.out_start) < EPS:
            spans[-1] = (spans[-1][0], entry.out_end)
        else:
            spans.append((entry.out_start, entry.out_end))
    return tuple(spans)


def clip_intervals(timeline: Sequence[TimelineEntry]) -> tuple[tuple[float, float], ...]:
    """Output-time spans of `original` lines, adjacent ones merged."""
    return _intervals(timeline, "original")


def narration_intervals(timeline: Sequence[TimelineEntry]) -> tuple[tuple[float, float], ...]:
    """Output-time spans of `narrate` lines, back-to-back ones merged."""
    return _intervals(timeline, "narrate")


def loop_plan(track_sec: float, total_sec: float) -> tuple[int, float]:
    """(copies, crossfade seconds). 0 copies = too short to crossfade: use -stream_loop."""
    if track_sec >= total_sec - EPS:
        return 1, 0.0
    xfade = min(LOOP_XFADE_SEC, track_sec / 4)
    copies = math.ceil((total_sec - xfade) / (track_sec - xfade))
    return (0, 0.0) if copies > MAX_LOOP_COPIES else (copies, xfade)


def _windows(bed: MusicBed, total: float) -> list[tuple[float, float]]:
    """Where each cue plays: its span, overlapping its neighbours by CUE_XFADE_SEC."""
    half = CUE_XFADE_SEC / 2
    last = len(bed.cues) - 1
    return [
        (max(0.0, c.out_start - (half if k > 0 else 0.0)), min(c.out_end, total) + (half if k < last else 0.0))
        for k, c in enumerate(bed.cues)
    ]


def music_inputs(bed: MusicBed, total_sec: float) -> list[str]:
    inputs: list[str] = []
    for cue, (start, end) in zip(bed.cues, _windows(bed, total_sec)):
        copies, _ = loop_plan(cue.track_sec, end - start)
        inputs += ["-stream_loop", "-1", "-i", str(cue.path)] if copies == 0 else ["-i", str(cue.path)] * copies
    return inputs


# ---- filter graph -----------------------------------------------------------------------


def _duck(start: float, end: float, factor: float, ramp_sec: float) -> str:
    """Multiply the level by `factor` in [start, end], ramping in and out."""
    ramp = _num(min(ramp_sec, (end - start) / 2))
    a, b = _num(start), _num(end)
    expr = f"1-(1-{_num(factor)})*min(1,(t-{a})/{ramp})*min(1,({b}-t)/{ramp})"
    return f"volume='{expr}':eval=frame:enable='between(t,{a},{b})'"


def _cue_chain(cue: MusicCue, first_input: int, k: int, last: int, window: tuple[float, float], out: str) -> tuple[str, int]:
    """Graph for one cue (looped, trimmed, faded at cue boundaries, delayed into place). Returns (graph, inputs used)."""
    start, end = window
    length = end - start
    copies, xfade = loop_plan(cue.track_sec, length)
    parts: list[str] = []
    label = f"[{first_input}:a]"
    for n in range(1, copies):
        parts.append(f"{label}[{first_input + n}:a]acrossfade=d={_num(xfade)}[mx{k}_{n}]")
        label = f"[mx{k}_{n}]"
    chain = [f"atrim=duration={_num(length)}", "asetpts=PTS-STARTPTS", f"aresample={AUDIO_RATE}",
             "aformat=channel_layouts=stereo"]  # fmt: skip
    if k > 0:
        chain.append(f"afade=t=in:d={_num(CUE_XFADE_SEC)}")
    if k < last:
        chain.append(f"afade=t=out:st={_num(length - CUE_XFADE_SEC)}:d={_num(CUE_XFADE_SEC)}")
    if last > 0:
        chain.append(f"adelay={round(start * MS_PER_SEC)}:all=1")
    parts.append(f"{label}{','.join(chain)}{out}")
    return ";".join(parts), max(copies, 1)


def music_filter(bed: MusicBed, timeline: Sequence[TimelineEntry], first_input: int) -> str:
    """filter_complex part turning the inputs from `music_inputs` into the `[mus]` bed."""
    total = timeline[-1].out_end if timeline else 0.0
    last = len(bed.cues) - 1
    parts: list[str] = []
    index = first_input
    for k, (cue, window) in enumerate(zip(bed.cues, _windows(bed, total))):
        graph, used = _cue_chain(cue, index, k, last, window, "[bed]" if last == 0 else f"[c{k}]")
        parts.append(graph)
        index += used
    if last > 0:
        labels = "".join(f"[c{k}]" for k in range(last + 1))
        parts.append(f"{labels}amix=inputs={last + 1}:duration=longest:normalize=0[bed]")

    voice = bed.voice_volume / bed.bed_volume if bed.bed_volume > 0 else 1.0
    fade_in = min(FADE_IN_SEC, total / 4)
    fade_out = min(FADE_OUT_SEC, total / 4)
    chain = [
        "apad",
        f"atrim=duration={_num(total)}",
        f"volume={_num(bed.bed_volume)}",
        *(_duck(a, b, voice, NARRATION_RAMP_SEC) for a, b in narration_intervals(timeline)),
        *(_duck(a, b, voice * CLIP_FACTOR, DUCK_RAMP_SEC) for a, b in clip_intervals(timeline)),
        f"afade=t=in:d={_num(fade_in)}",
        f"afade=t=out:st={_num(total - fade_out)}:d={_num(fade_out)}",
    ]
    parts.append(f"[bed]{','.join(chain)}[mus]")
    return ";".join(parts)
