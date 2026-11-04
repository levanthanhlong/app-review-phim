"""Beats the user marked in the Scene Browser (work/beat_marks.json: {beat_id: "star" | "skip" | "keep"}).

- star: important, flagged in the digest so the script includes it
- skip: left out of the digest, so the script cannot use it
- keep: used although the `inspect` stage wanted it skipped (ad, credits, outtakes...)

Beats in work/inspect.json `skip_beats` are skipped unless the user marked them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, TypeVar, get_args

from recap import config
from recap.models import Beat

MARKS_FILE = "work/beat_marks.json"
INSPECT_FILE = "work/inspect.json"
Mark = Literal["star", "skip", "keep"]
MARKS: tuple[str, ...] = get_args(Mark)

B = TypeVar("B", bound=Beat)


def load_marks(project_dir: Path) -> dict[str, Mark]:
    """The saved marks; a missing or damaged file means no marks."""
    try:
        data = json.loads((project_dir / MARKS_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(beat): mark for beat, mark in data.items() if mark in MARKS}


def save_marks(project_dir: Path, marks: Mapping[str, str]) -> None:
    """Write the marks (sorted); no marks left removes the file."""
    path = project_dir / MARKS_FILE
    if marks:
        config.write_json_atomic(path, dict(sorted(marks.items())))
    else:
        path.unlink(missing_ok=True)


def auto_skips(project_dir: Path) -> dict[str, str]:
    """{beat_id: reason} the `inspect` stage wants skipped; a missing or damaged report means none."""
    try:
        data = json.loads((project_dir / INSPECT_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    skips = data.get("skip_beats") if isinstance(data, dict) else None
    return {str(b): str(reason) for b, reason in skips.items()} if isinstance(skips, dict) else {}


def effective_marks(project_dir: Path) -> dict[str, Mark]:
    """The user's marks, plus "skip" for each beat `inspect` flagged that the user did not mark."""
    marks = load_marks(project_dir)
    return {**{beat: "skip" for beat in auto_skips(project_dir)}, **marks}


def marks_signature(project_dir: Path) -> str | None:
    """Fingerprint part for the script stage, from the marks themselves (not the file's mtime).
    None when nothing is marked or auto-skipped, so older fingerprints stay unchanged."""
    marks = effective_marks(project_dir)
    if not marks:
        return None
    payload = json.dumps(sorted(marks.items()))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def keep_marks(marks: Mapping[str, str], old: Sequence[Beat], new: Sequence[Beat]) -> dict[str, str]:
    """The marks still valid after the beats were rebuilt: only beats whose id and range are unchanged
    (a re-run of `scenes` reuses ids for different footage)."""
    before = {b.id: (b.start, b.end) for b in old}
    after = {b.id: (b.start, b.end) for b in new}
    return {beat: mark for beat, mark in marks.items() if beat in after and before.get(beat) == after[beat]}


def apply_marks(beats: Sequence[B], marks: Mapping[str, str]) -> tuple[list[B], frozenset[str]]:
    """(beats without the skipped ones, ids of the starred ones)."""
    kept = [b for b in beats if marks.get(b.id) != "skip"]
    starred = frozenset(b.id for b in kept if marks.get(b.id) == "star")
    return kept, starred
