"""CLI entry: python -m recap <command> ...

Commands:
  new      --title T --source FILE [--root DIR]     create a project folder
  doctor   [--root DIR]                             check environment
  set      --project DIR key=value ...              change settings (marks affected stages stale)
  voices   [--include-edge]                         list narration voices (for the voice picker)
  voice-preview --voice V [--engine E] [--rate R] [--pitch P] [--text T] --out FILE
                                                    synthesize a sample with that voice
  music-choose --project DIR --cue M01 --option N   use option N (0-based) of an AI music cue
  music-edit --project DIR --cue M01 [--prompt P] [--alt-prompt P] [--reroll]
                                                    change a cue's prompts / ask for new options
  <stage>  --project DIR [--force]                  run one stage
  run      --project DIR [--from S] [--to S] [--force]
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from dataclasses import asdict
from pathlib import Path

from recap import config, doctor, music_edit, settings_update, voices
from recap.events import Emitter, take_protocol_stdout
from recap.models import STAGE_NAMES
from recap.runner import Cancelled, install_signal_handlers, run_stage
from recap.stages import STAGES
from recap.tts_engines import TTSError, rate_factor

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CANCELLED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recap")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="tạo dự án mới")
    new.add_argument("--title", required=True)
    new.add_argument("--source", required=True, type=Path)
    new.add_argument("--root", type=Path, default=config.DEFAULT_PROJECTS_ROOT)

    doc = sub.add_parser("doctor", help="kiểm tra môi trường")
    doc.add_argument("--root", type=Path, default=config.DEFAULT_PROJECTS_ROOT)

    set_cmd = sub.add_parser("set", help="đổi settings của dự án")
    set_cmd.add_argument("--project", required=True, type=Path)
    set_cmd.add_argument("assignments", nargs="+", metavar="key=value")

    voice_list = sub.add_parser("voices", help="liệt kê giọng đọc")
    voice_list.add_argument("--include-edge", action="store_true", help="kèm giọng edge-tts (cần mạng, hay lỗi)")

    preview = sub.add_parser("voice-preview", help="tạo file nghe thử một giọng")
    preview.add_argument("--engine", choices=voices.ENGINES, default=voices.DEFAULT_ENGINE)
    preview.add_argument("--voice", default=voices.DEFAULT_VOICE)
    preview.add_argument("--rate", default="+0%")
    preview.add_argument("--pitch", default="+0Hz")
    preview.add_argument("--text", default="", help="câu đọc thử (mặc định: câu giới thiệu)")
    preview.add_argument("--out", required=True, type=Path)

    choose = sub.add_parser("music-choose", help="chọn bản nhạc AI cho một phần nhạc")
    choose.add_argument("--project", required=True, type=Path)
    choose.add_argument("--cue", required=True)
    choose.add_argument("--option", required=True, type=int)

    edit = sub.add_parser("music-edit", help="sửa prompt hoặc tạo lại các bản nhạc của một phần")
    edit.add_argument("--project", required=True, type=Path)
    edit.add_argument("--cue", required=True)
    edit.add_argument("--prompt")
    edit.add_argument("--alt-prompt", dest="alt_prompt")
    edit.add_argument("--reroll", action="store_true", help="tạo 3 bản mới (seed khác)")

    for name in STAGE_NAMES:
        stage = sub.add_parser(name, help=f"chạy stage {name}")
        stage.add_argument("--project", required=True, type=Path)
        stage.add_argument("--force", action="store_true")

    run = sub.add_parser("run", help="chạy nhiều stage liên tiếp")
    run.add_argument("--project", required=True, type=Path)
    run.add_argument("--from", dest="from_stage", choices=STAGE_NAMES, default=STAGE_NAMES[0])
    run.add_argument("--to", dest="to_stage", choices=STAGE_NAMES, default=STAGE_NAMES[-1])
    run.add_argument("--force", action="store_true")
    return parser


def cmd_new(args: argparse.Namespace, emitter: Emitter) -> int:
    try:
        project_dir = config.create_project(args.root, title=args.title, source=args.source)
    except config.ProjectError as exc:
        emitter.error(None, exc.code, exc.message, retryable=False)
        return EXIT_FAILED
    emitter.result("new", {"project": str(project_dir)})
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, emitter: Emitter) -> int:
    checks = doctor.run_checks(args.root)
    for c in checks:
        emitter.raw(f"{'✅' if c.ok else '❌'} {c.name}: {c.detail}" + (f"  → {c.hint}" if c.hint else ""))
    result = doctor.as_result(checks)
    emitter.result("doctor", result)
    return EXIT_OK if result["ok"] else EXIT_FAILED


def cmd_set(args: argparse.Namespace, emitter: Emitter) -> int:
    try:
        updates = settings_update.parse_assignments(args.assignments)
        settings, stale = settings_update.apply_settings(args.project, updates, STAGES)
    except settings_update.SettingsError as exc:
        emitter.error(None, "settings_invalid", str(exc), retryable=False)
        return EXIT_FAILED
    except config.ProjectError as exc:
        emitter.error(None, exc.code, exc.message, retryable=False)
        return EXIT_FAILED
    emitter.result("set", {"settings": settings.model_dump(mode="json"), "stale": stale})
    return EXIT_OK


def cmd_voices(args: argparse.Namespace, emitter: Emitter) -> int:
    shown = voices.all_voices() if args.include_edge else voices.vieneu_voices()
    catalog = [asdict(v) for v in shown]
    emitter.result("voices", {"voices": catalog, "default": {"engine": voices.DEFAULT_ENGINE, "voice": voices.DEFAULT_VOICE}})
    return EXIT_OK


def cmd_voice_preview(args: argparse.Namespace, emitter: Emitter) -> int:
    checks = (("voice_invalid", lambda: voices.check_voice(args.engine, args.voice)),
              ("settings_invalid", lambda: rate_factor(args.rate)))  # fmt: skip
    for code, check in checks:
        try:
            check()
        except TTSError as exc:
            emitter.error(None, code, str(exc), retryable=False)
            return EXIT_FAILED
    try:
        duration = voices.make_preview(args.engine, args.voice, args.rate, args.pitch, args.text, args.out)
    except TTSError as exc:
        emitter.error(None, "tts_failed", str(exc), retryable=True)
        return EXIT_FAILED
    emitter.result("voice-preview", {"file": str(args.out), "duration": round(duration, 3)})
    return EXIT_OK


def cmd_music(args: argparse.Namespace, emitter: Emitter) -> int:
    try:
        if args.command == "music-choose":
            stale = music_edit.choose_option(args.project, args.cue, args.option, STAGES)
        else:
            stale = music_edit.edit_cue(args.project, args.cue, STAGES, args.prompt, args.alt_prompt, args.reroll)
    except music_edit.MusicEditError as exc:
        emitter.error(None, "music_edit_invalid", str(exc), retryable=False)
        return EXIT_FAILED
    except config.ProjectError as exc:
        emitter.error(None, exc.code, exc.message, retryable=False)
        return EXIT_FAILED
    emitter.result(args.command, {"stale": stale})
    return EXIT_OK


def cmd_run(args: argparse.Namespace, emitter: Emitter) -> int:
    start, end = STAGE_NAMES.index(args.from_stage), STAGE_NAMES.index(args.to_stage)
    for name in STAGE_NAMES[start : end + 1]:
        if not run_stage(args.project, name, STAGES, emitter, force=args.force):
            return EXIT_FAILED
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    # Clients such as Foundation's Process pass arguments in NFD; voice names, titles and
    # catalogs are NFC. (APFS paths match either form.)
    raw = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args([unicodedata.normalize("NFC", a) for a in raw])
    emitter = Emitter(stream=take_protocol_stdout())
    install_signal_handlers()
    try:
        if args.command == "new":
            return cmd_new(args, emitter)
        if args.command == "doctor":
            return cmd_doctor(args, emitter)
        if args.command == "run":
            return cmd_run(args, emitter)
        if args.command == "set":
            return cmd_set(args, emitter)
        if args.command == "voices":
            return cmd_voices(args, emitter)
        if args.command == "voice-preview":
            return cmd_voice_preview(args, emitter)
        if args.command in ("music-choose", "music-edit"):
            return cmd_music(args, emitter)
        ok = run_stage(args.project, args.command, STAGES, emitter, force=args.force)
        return EXIT_OK if ok else EXIT_FAILED
    except Cancelled:
        return EXIT_CANCELLED


if __name__ == "__main__":
    sys.exit(main())
