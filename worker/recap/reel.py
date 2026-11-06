"""Plan and render a standalone vertical teaser from an assembled review."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

from recap import config, media, voices
from recap.events import Emitter
from recap.fit import TimelineEntry, timeline_from_json
from recap.llm import LLMError, LLMProvider, get_provider, model_key
from recap.models import Project, StageStatus
from recap.runner import StageFailure
from recap.script_model import load_script
from recap.stages.assemble import PREVIEW_OUT, TIMELINE
from recap.stages.export import FINAL_OUT
from recap.tts_engines import TTSError, wav_duration

WORK = "work/reel"
REEL_OUT = "output/recap_reel.mp4"
ENDING_OUT = f"{WORK}/ending.mp4"
MIN_CUT = 4 * 60.0
MAX_REEL = 5 * 60.0
CTA_RESERVE = 12.0
MAX_CUT = MAX_REEL - CTA_RESERVE
ENDING_SECONDS = 12.0
MAX_CTA_CHARS = 260


def candidate_cuts(entries: list[TimelineEntry]) -> list[TimelineEntry]:
    if not entries:
        return []
    # The full review must still have at least one line after the cut.
    available = [e for e in entries[:-1] if 0 < e.out_end <= MAX_CUT]
    window = [e for e in available if e.out_end >= MIN_CUT]
    return window or available


def default_cta(narrator: str) -> str:
    return f"{narrator.strip() or 'Mình'} để link bản review đầy đủ trong bình luận nha."


def rank_cuts(
    entries: list[TimelineEntry], cuts: list[TimelineEntry], texts: dict[str, str], narrator: str, provider: LLMProvider,
) -> dict[str, tuple[str, str]]:
    eligible = {entry.id for entry in cuts}
    # Include the lead-in and a little of what happens next, to assess what is still unresolved.
    last = max(i for i, entry in enumerate(entries) if entry.id in eligible)
    material = [
        {"id": entry.id, "eligible": entry.id in eligible, "text": texts.get(entry.id, entry.type)[:450]}
        for entry in entries[:last + 4]
    ]
    prompt = (
        "Chọn tối đa 3 điểm kết hay nhất cho Reel mở đầu review phim, xếp theo thứ tự hay nhất trước. "
        "Mỗi mốc là ngay SAU dòng có eligible=true. Ưu tiên tình tiết vừa xảy ra có câu hỏi bỏ ngỏ, "
        "người xem vẫn hiểu bối cảnh. Tránh cắt trước khi có tình tiết đủ rõ. "
        "Trả mảng JSON [{\"id\":\"...\",\"reason\":\"lý do ngắn cho biên tập viên\","
        "\"lead\":\"một câu gợi tò mò bằng tiếng Việt, tối đa 14 từ\"}]. "
        "Câu lead dựa trên những gì khán giả đã nghe tính tới mốc cắt. "
        "Không bịa, không tiết lộ tình tiết sau mốc và không tự thêm lời kêu gọi."
    )
    answer = provider.generate(prompt, json.dumps(material, ensure_ascii=False))
    fenced = re.search(r"```(?:json)?\s*(.*?)```", answer, re.DOTALL)
    try:
        raw = json.loads(fenced.group(1) if fenced else answer)
    except ValueError:
        return {}
    ranked: dict[str, tuple[str, str]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        cut, reason, lead = (item.get(field) for field in ("id", "reason", "lead"))
        if not isinstance(cut, str) or cut not in eligible or cut in ranked:
            continue
        if not all(isinstance(value, str) and value.strip() for value in (reason, lead)):
            continue
        cta = f"{lead.strip()} {default_cta(narrator)}"
        if len(cta) <= MAX_CTA_CHARS:
            ranked[cut] = (reason.strip()[:300], cta)
        if len(ranked) == 3:
            break
    return ranked


def _inputs(directory: Path, project: Project) -> tuple[list[TimelineEntry], dict[str, str], str]:
    preview = directory / PREVIEW_OUT
    if project.stages["assemble"].status != StageStatus.DONE or not preview.is_file():
        raise StageFailure("reel_missing", "Hãy ghép lại bản xem trước trước khi tạo Reel.")
    timeline, script = directory / TIMELINE, directory / "script.yaml"
    try:
        entries = timeline_from_json(json.loads(timeline.read_text(encoding="utf-8")))
        _, items = load_script(script)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StageFailure("reel_missing", "Không đọc được timeline hoặc kịch bản. Hãy ghép lại bản xem trước.") from exc
    texts = {item["id"]: item.get("text") or item.get("note") or "Giữ thoại gốc" for item in items}
    if not candidate_cuts(entries) or any(entry.id not in texts for entry in entries):
        raise StageFailure("reel_no_cut", "Bản review cần có nhiều đoạn hoàn chỉnh và timeline khớp với kịch bản.")
    settings = project.settings
    source = _video(directory, project)
    parts = [config.file_signature(path) for path in (timeline, script, preview, source)]
    parts.extend([model_key(settings), settings.llm_provider, settings.narrator, *_voice_settings(project)])
    revision = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
    return entries, texts, revision


def _voice_settings(project: Project) -> tuple[str, str, str, str]:
    s = project.settings
    return s.tts_engine, s.voice, s.tts_rate, s.tts_pitch


def plan(directory: Path, project: Project, only_cut: str | None = None) -> dict:
    entries, texts, revision = _inputs(directory, project)
    cuts = candidate_cuts(entries)
    if only_cut is not None:
        cuts = [cut for cut in cuts if cut.id == only_cut]
        if not cuts:
            raise StageFailure("reel_cut_invalid", "Mốc cắt không còn hợp lệ. Hãy mở lại Tạo Reel.")

    narrator = project.settings.narrator
    warning = None
    try:
        ranked = rank_cuts(entries, cuts, texts, narrator, get_provider(project.settings))
        if not ranked:
            warning = "Model chưa đưa ra gợi ý hợp lệ. Bạn có thể tự chọn mốc và sửa câu dẫn."
    except LLMError as exc:
        ranked = {}
        warning = f"Không lấy được gợi ý AI ({exc.message}). Bạn vẫn có thể chọn mốc và sửa câu dẫn."
    target = (MIN_CUT + MAX_CUT) / 2
    if not any(e.out_end >= MIN_CUT for e in cuts):
        target = min(target, entries[-1].out_end * 0.7)
        note = "Bản review chưa có mốc cắt phù hợp trong khoảng 4–5 phút. Reel có thể ngắn hơn."
        warning = f"{note} {warning}" if warning else note
    fallback = min(cuts, key=lambda entry: abs(entry.out_end - target))
    return {
        "revision": revision,
        "selected": next(iter(ranked), fallback.id),
        "warning": warning,
        "cuts": [
            {"id": e.id, "time": e.out_end, "text": texts[e.id], "reason": ranked[e.id][0] if e.id in ranked else None,
             "cta": ranked[e.id][1] if e.id in ranked else default_cta(narrator)}
            for e in cuts
        ],
    }


def _video(directory: Path, project: Project) -> Path:
    preview, final = directory / PREVIEW_OUT, directory / FINAL_OUT
    if (project.stages["export"].status == StageStatus.DONE and final.is_file()
            and final.stat().st_mtime_ns >= preview.stat().st_mtime_ns):
        return final
    return preview


def render_args(source: Path, voice: Path, start: float, end: float, voice_seconds: float, out: Path) -> list[str]:
    length = end - start
    graph = (
        "[0:v]fps=30,setsar=1,split[film][bg];"
        "[bg]scale=180:320:force_original_aspect_ratio=increase,crop=180:320,gblur=sigma=12,"
        "scale=1080:1920,setsar=1[back];"
        "[film]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos[front];"
        "[back][front]overlay=(W-w)/2:(H-h)/2,"
        f"tpad=stop_mode=clone:stop_duration={voice_seconds:.3f},format=yuv420p[v];"
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=duration={length:.3f}[opening];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo[cta];"
        "[opening][cta]concat=n=2:v=0:a=1[a]"
    )
    return [
        "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(source), "-i", str(voice),
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{length + voice_seconds:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", str(out),
    ]


def render(
    directory: Path, project: Project, cut_id: str, cta: str, revision: str, ending: bool, emitter: Emitter,
) -> dict:
    entries, _, current_revision = _inputs(directory, project)
    if revision != current_revision:
        raise StageFailure("reel_changed", "Bản review đã thay đổi. Hãy mở lại Tạo Reel để lấy mốc mới.")
    cut = next((entry for entry in candidate_cuts(entries) if entry.id == cut_id), None)
    if cut is None:
        raise StageFailure("reel_cut_invalid", "Mốc cắt không hợp lệ.")
    cta = " ".join(cta.split())
    if not cta or len(cta) > MAX_CTA_CHARS:
        raise StageFailure("reel_cta_invalid", f"Câu dẫn cần từ 1 đến {MAX_CTA_CHARS} ký tự.")

    folder = directory / WORK
    folder.mkdir(parents=True, exist_ok=True)
    voice_key = hashlib.sha256(json.dumps([*_voice_settings(project), cta], ensure_ascii=False).encode()).hexdigest()[:20]
    voice = folder / f"cta-{voice_key}.wav"
    source = _video(directory, project)
    start = max(0, cut.out_end - ENDING_SECONDS) if ending else 0.0
    output = directory / (ENDING_OUT if ending else REEL_OUT)
    with tempfile.TemporaryDirectory(dir=folder) as tmp, emitter.stage_log(directory / "logs/reel.log") as log:
        scratch = Path(tmp)
        if voice.is_file():
            duration = wav_duration(voice)
        else:
            config.configure_cache_env()
            emitter.log("info", "Tạo giọng đọc cho câu dẫn Reel")
            temp_voice = scratch / "cta.wav"
            duration = voices.make_preview(*_voice_settings(project), cta, temp_voice)
            temp_voice.replace(voice)
        if cut.out_end + duration > MAX_REEL:
            raise StageFailure("reel_too_long", "Reel dài hơn 5 phút. Hãy rút ngắn câu dẫn hoặc chọn mốc sớm hơn.")
        total = cut.out_end - start + duration
        temp_output = scratch / "reel.mp4"
        media.run_ffmpeg(
            render_args(source, voice, start, cut.out_end, duration, temp_output), total,
            lambda sec: emitter.progress("reel", sec, total, "Render Reel"), log,
        )
        temp_output.replace(output)
    return {"file": str(output), "duration": round(total, 3), "source": "final" if source.name == Path(FINAL_OUT).name else "preview"}
