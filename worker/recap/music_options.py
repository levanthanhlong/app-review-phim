"""Generating the 3 audio options of each music cue (cached), and the provenance record.

Options are cached by everything that shapes the audio (engine, model, prompt, seed, length), so
editing one cue's prompt only regenerates the options that changed. Engines are tried in order:
when one fails the next is used, and the record says which one made each file.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from recap.models import now_iso
from recap.music_gen import (
    INSTRUMENTAL,
    Generator,
    GenRequest,
    MusicGenError,
    OptionSpec,
    gen_seconds,
    option_specs,
)

AUDIO_DIR = "music"  # under work/
Option = dict[str, Any]
Warn = Callable[[str], None]


class OptionsFailed(Exception):
    pass


def _key(gen: Generator, req: GenRequest) -> str:
    payload = json.dumps([gen.engine, gen.model_name(req), req.prompt, req.seed, req.seconds], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _file_name(cue_id: str, spec: OptionSpec, gen: Generator, req: GenRequest) -> str:
    return f"{cue_id}_{spec.label}_{gen.engine}_{_key(gen, req)}.wav"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(work: Path, name: str, spec: OptionSpec, gen: Generator, req: GenRequest, old: dict[str, Option]) -> Option:
    rel = f"{AUDIO_DIR}/{name}"
    previous = old.get(rel, {})
    return {
        "label": spec.label,
        "prompt": spec.prompt,
        "model_prompt": req.prompt,
        "seed": spec.seed,
        "seconds": req.seconds,
        "engine": gen.engine,
        "model": gen.model_name(req),
        "file": rel,
        "sha256": previous.get("sha256") or _sha256(work / rel),
        "created_at": previous.get("created_at") or now_iso(),
    }


@dataclass(frozen=True)
class _Wanted:
    cue: int  # index in the plan
    spec: OptionSpec
    req: GenRequest


def _cached(work: Path, cue_id: str, item: _Wanted, gens: Sequence[Generator]) -> tuple[Generator, str] | None:
    for gen in gens:
        name = _file_name(cue_id, item.spec, gen, item.req)
        if (work / AUDIO_DIR / name).is_file():
            return gen, name
    return None


def _run_engine(gen: Generator, items: list[tuple[GenRequest, Path]], log: IO[str], on_done: Callable[[Path], None]) -> None:
    """All items in one call when the engine supports it (one model load), else one by one."""
    batch = getattr(gen, "generate_batch", None)
    if batch is not None:
        batch(items, log, on_done)
        return
    for req, out in items:
        gen.generate(req, out, log)
        on_done(out)


def all_options(
    work: Path, cues: Sequence[dict[str, Any]], model: str, gens: Sequence[Generator], log: IO[str], warn: Warn,
    on_option: Callable[[str], None],
) -> tuple[list[list[Option]], int]:  # fmt: skip
    """The 3 options of every plan cue. Missing ones are made by the first engine in one batch; what it
    could not make goes to the next engine. Returns (records per cue, how many were newly generated)."""
    wanted: list[_Wanted] = []
    for index, cue in enumerate(cues):
        seconds = gen_seconds(float(cue["out_end"]) - float(cue["out_start"]))
        for spec in option_specs(cue["id"], cue["prompt"], cue["alt_prompt"], int(cue.get("round", 0))):
            wanted.append(_Wanted(index, spec, GenRequest(spec.prompt + INSTRUMENTAL, seconds, spec.seed, model)))

    made: dict[int, tuple[Generator, str]] = {}  # position in `wanted` -> (engine, file name)
    for n, item in enumerate(wanted):
        hit = _cached(work, cues[item.cue]["id"], item, gens)
        if hit is not None:
            made[n] = hit
            on_option(f"{cues[item.cue]['id']} bản {item.spec.label} (có sẵn)")

    errors: list[str] = []
    fresh = [0]  # newly generated, counted as they finish
    for gen in gens:
        todo = [n for n in range(len(wanted)) if n not in made]
        if not todo:
            break
        outs = {n: work / AUDIO_DIR / _file_name(cues[wanted[n].cue]["id"], wanted[n].spec, gen, wanted[n].req) for n in todo}
        tmps = {n: out.with_name(f"{out.stem}.tmp.wav") for n, out in outs.items()}
        by_tmp = {tmp: n for n, tmp in tmps.items()}

        def done(tmp: Path, gen: Generator = gen, outs: dict[int, Path] = outs, by_tmp: dict[Path, int] = by_tmp) -> None:
            n = by_tmp[tmp]
            os.replace(tmp, outs[n])
            made[n] = (gen, outs[n].name)
            fresh[0] += 1
            on_option(f"{cues[wanted[n].cue]['id']} bản {wanted[n].spec.label}")

        try:
            _run_engine(gen, [(wanted[n].req, tmps[n]) for n in todo], log, done)
        except MusicGenError as exc:
            left = sum(1 for n in todo if n not in made)
            errors.append(f"{gen.engine}: {exc}")
            warn(f"{gen.engine} lỗi ({exc}), còn {left} bản nhạc chuyển sang engine khác")
        finally:
            for tmp in tmps.values():
                tmp.unlink(missing_ok=True)

    missing = [n for n in range(len(wanted)) if n not in made]
    if missing:
        first = wanted[missing[0]]
        raise OptionsFailed(f"{cues[first.cue]['id']} bản {first.spec.label}: không engine nào tạo được nhạc "
                            f"({'; '.join(errors) or 'không có engine'})")  # fmt: skip

    records: list[list[Option]] = [[] for _ in cues]
    for n, item in enumerate(wanted):
        gen, name = made[n]
        old = {o["file"]: o for o in cues[item.cue].get("options", [])}
        records[item.cue].append(_record(work, name, item.spec, gen, item.req, old))
    return records, fresh[0]


def remove_unused(work: Path, cues: Sequence[dict[str, Any]]) -> None:
    keep = {Path(o["file"]).name for c in cues for o in c.get("options", [])}
    for wav in (work / AUDIO_DIR).glob("*.wav"):
        if wav.name not in keep:
            wav.unlink()


def provenance(title: str, cues: Sequence[dict[str, Any]], gens: Sequence[Generator]) -> dict[str, Any]:
    """Proof of origin for the AI music: prompts, seeds, models, licenses and file hashes."""
    used = {o["engine"] for c in cues for o in c.get("options", [])}
    return {
        "project": title,
        "written_at": now_iso(),
        "note": "Nhạc nền do AI tạo trên máy, prompt không chứa tên phim, nhân vật hay nghệ sĩ.",
        "engines": {g.engine: g.provenance() for g in gens if g.engine in used},
        "cues": [
            {
                "id": c["id"],
                "out_start": c["out_start"],
                "out_end": c["out_end"],
                "mood": c["mood"],
                "chosen": c["options"][c.get("chosen", 0)] if c.get("options") else None,
                "options": c.get("options", []),
            }
            for c in cues
        ],
    }
