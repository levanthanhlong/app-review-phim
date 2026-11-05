"""Characters (pure): faces grouped into people, the beats each one is in, and the user's naming.

The `characters` stage finds the faces (`faces.py`); the user names the people in the app's
"Nhân vật" tab (work/character_choices.json); the script stage then lists, for every beat, the
names of who is on screen, so Claude calls everyone by the right name.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from recap import config
from recap.models import Beat

CHOICES_FILE = "work/character_choices.json"
CLUSTER_DISTANCE = 0.65  # average cosine distance under which faces are the same person (SFace)
GENDERS = ("nam", "nữ")
MIN_FACES = 3  # people seen in fewer sampled frames are extras, not offered for naming
SAMPLES = 6  # face pictures kept per character


@dataclass(frozen=True)
class Face:
    time: float  # seconds in the film
    box: tuple[float, float, float, float]  # x, y, w, h as fractions of the frame
    score: float
    feat: np.ndarray = field(compare=False, repr=False)  # unit-length SFace feature


@dataclass(frozen=True)
class Character:
    id: str  # C01 = most screen time
    faces: tuple[int, ...]  # indices into the face list
    screen_sec: float
    beats: tuple[str, ...]  # beats the person is seen in, in film order
    samples: tuple[int, ...]  # best faces for the pictures (big and sure first)


@dataclass(frozen=True)
class Choices:
    confirmed: bool = False  # the user saved the naming (or chose to skip it)
    names: Mapping[str, str] = field(default_factory=dict)  # character id -> name
    merged: Mapping[str, str] = field(default_factory=dict)  # character id -> the one it is the same person as
    ignored: frozenset[str] = frozenset()  # extras the script should not name
    genders: Mapping[str, str] = field(default_factory=dict)  # character id -> "nam" | "nữ" (for proper names)


def cluster_faces(faces: Sequence[Face], distance: float = CLUSTER_DISTANCE) -> list[list[int]]:
    """Groups of face indices that are the same person, biggest first (ties: seen first)."""
    if not faces:
        return []
    if len(faces) == 1:
        return [[0]]
    from scipy.cluster.hierarchy import fcluster, linkage

    labels = fcluster(linkage(np.stack([f.feat for f in faces]), method="average", metric="cosine"),
                      t=distance, criterion="distance")  # fmt: skip
    groups: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(int(label), []).append(index)
    return sorted(groups.values(), key=lambda g: (-len(g), faces[g[0]].time))


def _beats_of(times: Sequence[float], beats: Sequence[Beat]) -> tuple[str, ...]:
    return tuple(b.id for b in beats if any(b.start <= t < b.end for t in times))


def build_characters(
    faces: Sequence[Face], groups: Sequence[Sequence[int]], beats: Sequence[Beat], interval: float, min_faces: int = MIN_FACES
) -> list[Character]:  # fmt: skip
    """The people worth naming, most screen time first, with the beats they are in."""
    kept = [g for g in groups if len(g) >= min_faces]
    kept.sort(key=lambda g: (-len(g), faces[g[0]].time))
    return [
        Character(
            id=f"C{n:02d}",
            faces=tuple(g),
            screen_sec=len(g) * interval,
            beats=_beats_of([faces[i].time for i in g], beats),
            samples=tuple(sorted(g, key=lambda i: -(faces[i].box[2] * faces[i].box[3] * faces[i].score))[:SAMPLES]),
        )
        for n, g in enumerate(kept, start=1)
    ]  # fmt: skip


# ---- the user's naming -------------------------------------------------------------------------


def _resolve(character: str, merged: Mapping[str, str]) -> str:
    """The character a (possibly merged) one stands for; a merge loop keeps the character itself."""
    seen, current = {character}, character
    while current in merged:
        current = merged[current]
        if current in seen:
            return character
        seen.add(current)
    return current


def _name_of(character: str, choices: Choices) -> str | None:
    """The name the script uses, with the gender when the user gave one: "Cha (nữ)"."""
    target = _resolve(character, choices.merged)
    if character in choices.ignored or target in choices.ignored:
        return None
    name = (choices.names.get(target) or "").strip()
    if not name:
        return None
    gender = choices.genders.get(target)
    return f"{name} ({gender})" if gender in GENDERS else name


def cast_by_beat(characters: Sequence[Character], choices: Choices) -> dict[str, tuple[str, ...]]:
    """Beat id -> names of the named people on screen (most screen time first, each name once)."""
    cast: dict[str, list[str]] = {}
    for character in characters:  # already in screen-time order
        name = _name_of(character.id, choices)
        if name is None:
            continue
        for beat in character.beats:
            names = cast.setdefault(beat, [])
            if name not in names:
                names.append(name)
    return {beat: tuple(names) for beat, names in cast.items()}


def cast_list(characters: Sequence[Character], choices: Choices) -> list[tuple[str, float]]:
    """(name, seconds on screen) of everyone named, most screen time first."""
    total: dict[str, float] = {}
    for character in characters:
        name = _name_of(character.id, choices)
        if name is not None:
            total[name] = total.get(name, 0.0) + character.screen_sec
    return sorted(total.items(), key=lambda item: -item[1])


def load_choices(project_dir: Path) -> Choices:
    """The saved naming; a missing or damaged file means nothing chosen yet."""
    try:
        data = json.loads((project_dir / CHOICES_FILE).read_text(encoding="utf-8"))
        return Choices(
            confirmed=bool(data.get("confirmed", False)),
            names={str(k): str(v) for k, v in dict(data.get("names", {})).items()},
            merged={str(k): str(v) for k, v in dict(data.get("merged", {})).items()},
            ignored=frozenset(str(i) for i in data.get("ignored", [])),
            genders={str(k): str(v) for k, v in dict(data.get("genders", {})).items()},
        )
    except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
        return Choices()


def save_choices(project_dir: Path, choices: Choices) -> None:
    config.write_json_atomic(project_dir / CHOICES_FILE, {
        "confirmed": choices.confirmed,
        "names": dict(sorted(choices.names.items())),
        "merged": dict(sorted(choices.merged.items())),
        "ignored": sorted(choices.ignored),
        "genders": dict(sorted(choices.genders.items())),
    })  # fmt: skip


def cast_signature(cast: Mapping[str, Sequence[str]]) -> str | None:
    """Fingerprint part for the script stage; None when nobody is named (older fingerprints hold)."""
    if not cast:
        return None
    payload = json.dumps(sorted((beat, list(names)) for beat, names in cast.items()), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---- for the script stage ----------------------------------------------------------------------

CHARACTERS_FILE = "work/characters.json"
UNNAMED = (
    "Chưa đặt tên nhân vật: mở tab “Nhân vật” để chọn tên cho từng người, hoặc bấm “Bỏ qua” "
    "để máy tự gọi tên, rồi chạy tiếp."
)


@dataclass(frozen=True)
class Cast:
    by_beat: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # beat id -> names on screen
    names: tuple[str, ...] = ()  # everyone named, most screen time first
    needs_naming: bool = False  # people were found but the user has not saved (or skipped) the naming


def load_characters(project_dir: Path) -> list[Character]:
    """The people the `characters` stage found (none when it has not run or its file is damaged)."""
    try:
        data = json.loads((project_dir / CHARACTERS_FILE).read_text(encoding="utf-8"))
        return [Character(id=str(c["id"]), faces=(), screen_sec=float(c["screen_sec"]), beats=tuple(c["beats"]), samples=())
                for c in data["characters"]]  # fmt: skip
    except (OSError, ValueError, KeyError, TypeError):
        return []


def project_cast(project_dir: Path) -> Cast:
    characters = load_characters(project_dir)
    if not characters:
        return Cast()
    choices = load_choices(project_dir)
    if not choices.confirmed:
        return Cast(needs_naming=True)
    return Cast(by_beat=cast_by_beat(characters, choices), names=tuple(n for n, _ in cast_list(characters, choices)))
