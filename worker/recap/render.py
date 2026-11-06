"""ffmpeg argument builders for rendering timeline entries and joining them.

Each timeline entry is rendered to its own intermediate file (H.264 + PCM in .mkv, all
with identical parameters) so entries can be cached individually and joined with the
concat demuxer without re-encoding video. Loudness is normalized once, on the join.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from recap.branding import BG_COLOR, BrandLayout
from recap.cleanup import SourceCleanup, cleanup_filter
from recap.fit import TimelineEntry

FADE_SEC = 0.2
NARRATION_DELAY_SEC = 0.1  # part of PAD_SEC: narration starts slightly after the cut
AUDIO_RATE = 48000
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"  # YouTube/Facebook play at about -14 LUFS
SHARPEN = "unsharp=5:5:0.5:5:5:0"  # light luma sharpening, for footage scaled up


@dataclass(frozen=True)
class RenderProfile:
    name: str
    width: int
    height: int
    crf: int
    preset: str
    video_bitrate: str | None = None  # e.g. "10M": average bitrate instead of constant quality (crf)
    max_bitrate: str | None = None
    sharpen: bool = False

    def video_codec_args(self) -> list[str]:
        rate = (["-b:v", self.video_bitrate, "-maxrate", self.max_bitrate or self.video_bitrate,
                 "-bufsize", self.max_bitrate or self.video_bitrate]
                if self.video_bitrate else ["-crf", str(self.crf)])  # fmt: skip
        return ["-c:v", "libx264", "-preset", self.preset, *rate, "-pix_fmt", "yuv420p"]


PREVIEW = RenderProfile("preview", 854, 480, crf=23, preset="veryfast")


def _num(value: float) -> str:
    return f"{value:.6g}"


def duck_expression(duration: float, duck: float, fade_in: bool, fade_out: bool) -> str:
    """Volume of the film sound under narration: `duck`, ramping from/to 1.0 at the requested edges."""
    d, f = _num(duck), _num(FADE_SEC)
    expr = d
    if fade_out:
        edge = _num(duration - FADE_SEC)
        expr = f"if(gt(t,{edge}),{d}+(1-{d})*(t-{edge})/{f},{expr})"
    if fade_in:
        expr = f"if(lt(t,{f}),1-(1-{d})*t/{f},{expr})"
    return expr


def _fit_filter(profile: RenderProfile, layout: BrandLayout | None, keep_subtitle_bar: bool = False) -> str:
    """Scale the film (lanczos) into the frame: whole frame, or the branding layout's box, which it
    either fills (cropping the overflow: no black bars) or fits inside."""
    w, h = profile.width, profile.height
    if keep_subtitle_bar:
        # Place any padding below the film and reserve space for the branding bands.
        bw, x, y, bottom = (layout.video_w, layout.video_x, layout.top, layout.bottom) if layout else (w, 0, 0, 0)
        color = "0x" + "".join(f"{c:02X}" for c in BG_COLOR) if layout else "black"
        return (f"scale={bw}:{h - y - bottom}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,"
                f"pad={w}:{h}:{x}+({bw}-iw)/2:{y}:color={color}")
    if layout is None:
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
    bw, bh, x, y = layout.video_w, layout.video_h, layout.video_x, layout.video_y
    color = "0x" + "".join(f"{c:02X}" for c in BG_COLOR)
    if layout.fill:
        return f"scale={bw}:{bh}:force_original_aspect_ratio=increase:flags=lanczos,crop={bw}:{bh},pad={w}:{h}:{x}:{y}:color={color}"
    return (f"scale={bw}:{bh}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:{x}+({bw}-iw)/2:{y}+({bh}-ih)/2:color={color}")  # fmt: skip


def _segment_filters(
    index: int, start: float, end: float, speed: float, profile: RenderProfile, fps: float, audio_stream: int,
    layout: BrandLayout | None = None, cleanup: SourceCleanup = SourceCleanup(),
) -> str:  # fmt: skip
    length = _num(end - start)
    clean = cleanup_filter(cleanup, f"s{index}", start, end)  # input seeked to `start`: t is 0 there
    video = (
        f"[{index}:v]{clean + ',' if clean else ''}trim=duration={length},setpts=(PTS-STARTPTS)/{_num(speed)},fps={_num(fps)},"
        f"{_fit_filter(profile, layout, cleanup.keep_subtitle_bar)}{',' + SHARPEN if profile.sharpen else ''},"
        f"setsar=1,format=yuv420p[v{index}]"
    )
    tempo = f",atempo={_num(speed)}" if speed != 1.0 else ""
    audio = (
        f"[{index}:a:{audio_stream}]atrim=duration={length},asetpts=PTS-STARTPTS{tempo},"
        f"aresample={AUDIO_RATE},aformat=channel_layouts=stereo[a{index}]"
    )
    return f"{video};{audio}"


def item_args(
    entry: TimelineEntry,
    source: Path,
    tts_path: Path | None,
    profile: RenderProfile,
    fps: float,
    duck: float,
    fade_in: bool,
    fade_out: bool,
    out: Path,
    audio_stream: int = 0,
    brand: tuple[BrandLayout, Path] | None = None,
    cleanup: SourceCleanup = SourceCleanup(),
) -> list[str]:
    """ffmpeg args (without the leading `ffmpeg`) rendering one timeline entry to `out`.

    `audio_stream` is the index among the source's audio streams (the film's chosen track).
    `brand` = (layout, frame PNG): the film goes in the layout's box and the frame is laid on top.
    `cleanup`: the source's watermarks blurred and black bars cropped, before scaling.
    """
    layout = brand[0] if brand else None
    duration = _num(entry.out_end - entry.out_start)
    inputs: list[str] = []
    filters: list[str] = []
    for i, seg in enumerate(entry.segments):
        inputs += ["-ss", f"{seg.src_start:.3f}", "-t", f"{seg.src_end - seg.src_start:.3f}", "-i", str(source)]
        filters.append(_segment_filters(i, seg.src_start, seg.src_end, seg.speed, profile, fps, audio_stream, layout,
                                        cleanup))
    n = len(entry.segments)
    pairs = "".join(f"[v{i}][a{i}]" for i in range(n))
    filters.append(f"{pairs}concat=n={n}:v=1:a=1[vc][ac]")
    # Always pad then cut to the exact length: holds the last frame when footage is short.
    held = f"[vc]tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration}"
    filters.append(f"{held}[vout]" if brand is None else f"{held}[vbase]")

    if tts_path is None:
        filters.append(f"[ac]apad,atrim=duration={duration}[aout]")
    else:
        inputs += ["-i", str(tts_path)]
        delay_ms = round(NARRATION_DELAY_SEC * 1000)
        volume = duck_expression(entry.out_end - entry.out_start, duck, fade_in, fade_out)
        filters.append(f"[ac]apad,atrim=duration={duration},volume='{volume}':eval=frame[bg]")
        filters.append(
            f"[{n}:a]aresample={AUDIO_RATE},aformat=channel_layouts=stereo,adelay={delay_ms}:all=1,"
            f"apad,atrim=duration={duration}[nar]"
        )
        filters.append("[bg][nar]amix=inputs=2:duration=first:normalize=0[aout]")

    if brand is not None:
        frame_input = n + (1 if tts_path is not None else 0)
        inputs += ["-i", str(brand[1])]
        filters.append(f"[vbase][{frame_input}:v]overlay=0:0,format=yuv420p[vout]")

    return [
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        *profile.video_codec_args(),
        "-r", _num(fps),
        "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE),
        str(out),
    ]  # fmt: skip


def concat_args(
    list_file: Path, out: Path, music_inputs: list[str] | None = None, music_graph: str | None = None
) -> list[str]:
    """Join intermediate files (video copied) and normalize loudness to -14 LUFS.

    With music, `music_graph` (see `recap.music.music_filter`, inputs numbered from 1) must
    produce `[mus]`, which is mixed under the joined audio before normalizing.
    """
    if music_inputs and music_graph:
        mixed = f"{music_graph};[0:a][mus]amix=inputs=2:duration=first:normalize=0,{LOUDNORM}[aout]"
        audio = [*music_inputs, "-map", "0:v", "-map", "[aout]", "-filter_complex", mixed]
    else:
        audio = ["-map", "0:v", "-map", "0:a", "-af", LOUDNORM]
    return [
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        *audio,
        "-c:v", "copy",
        "-ar", str(AUDIO_RATE), "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip
