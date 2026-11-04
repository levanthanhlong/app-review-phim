"""Beat marks: reading the file and keeping marks valid when the beats are rebuilt."""

from __future__ import annotations

import json
from pathlib import Path

from recap.beat_marks import (
    INSPECT_FILE,
    MARKS_FILE,
    apply_marks,
    auto_skips,
    effective_marks,
    keep_marks,
    load_marks,
    marks_signature,
    save_marks,
)
from recap.models import Beat


def beat(beat_id: str, start: float, end: float) -> Beat:
    return Beat(id=beat_id, start=start, end=end, shots=[(start, end)], keyframe=f"keyframes/{beat_id}.jpg")


def test_unreadable_marks_file_means_no_marks(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    (tmp_path / MARKS_FILE).write_bytes(b"\xff\xfe not utf-8")

    assert load_marks(tmp_path) == {}


def test_marks_on_unchanged_beats_are_kept_others_dropped() -> None:
    old = [beat("B0001", 0, 10), beat("B0002", 10, 20), beat("B0003", 20, 30)]
    new = [beat("B0001", 0, 10), beat("B0002", 10, 16), beat("B0003", 16, 30)]
    marks = {"B0001": "star", "B0002": "skip", "B0003": "star", "B0009": "skip"}

    assert keep_marks(marks, old, new) == {"B0001": "star"}


def test_no_previous_beats_drops_every_mark() -> None:
    assert keep_marks({"B0001": "star"}, [], [beat("B0001", 0, 10)]) == {}


def write_inspect(project_dir: Path, skip_beats: dict[str, str]) -> None:
    (project_dir / "work").mkdir(exist_ok=True)
    (project_dir / INSPECT_FILE).write_text(json.dumps({"skip_beats": skip_beats}), encoding="utf-8")


def test_beats_found_by_inspect_are_skipped_unless_the_user_keeps_or_stars_them(tmp_path: Path) -> None:
    # Arrange: inspect wants B0001-B0003 gone; the user keeps B0002 and stars B0003
    write_inspect(tmp_path, {"B0001": "quảng cáo", "B0002": "credit", "B0003": "credit"})
    save_marks(tmp_path, {"B0002": "keep", "B0003": "star", "B0004": "skip"})
    beats = [beat(f"B000{i}", i * 10, i * 10 + 10) for i in range(1, 6)]

    # Act
    kept, starred = apply_marks(beats, effective_marks(tmp_path))

    # Assert
    assert [b.id for b in kept] == ["B0002", "B0003", "B0005"]
    assert starred == frozenset({"B0003"})


def test_auto_skips_are_read_with_their_reason_and_a_damaged_report_means_none(tmp_path: Path) -> None:
    write_inspect(tmp_path, {"B0001": "quảng cáo: Trung Nguyên"})
    assert auto_skips(tmp_path) == {"B0001": "quảng cáo: Trung Nguyên"}

    (tmp_path / INSPECT_FILE).write_text("{not json", encoding="utf-8")
    assert auto_skips(tmp_path) == {}


def test_signature_follows_what_is_skipped_not_only_the_user_marks(tmp_path: Path) -> None:
    assert marks_signature(tmp_path) is None  # nothing marked, nothing inspected: old fingerprints hold

    write_inspect(tmp_path, {"B0001": "credit"})
    auto = marks_signature(tmp_path)
    save_marks(tmp_path, {"B0001": "keep"})

    assert auto is not None and marks_signature(tmp_path) not in (None, auto)
