"""Characters (pure): faces grouped into people, the beats each one is in, the user's naming."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from recap.characters import (
    Choices,
    Face,
    build_characters,
    cast_by_beat,
    cast_list,
    cluster_faces,
    load_choices,
    save_choices,
)
from recap.models import Beat

RNG = np.random.default_rng(7)
PEOPLE = {name: RNG.normal(size=128) for name in ("pan", "hiro", "mother", "extra")}


def face(person: str, time: float, noise: float = 0.25) -> Face:
    feat = PEOPLE[person] + RNG.normal(scale=noise, size=128)
    return Face(time=time, box=(0.4, 0.3, 0.1, 0.15), score=0.95, feat=feat / np.linalg.norm(feat))


def beat(beat_id: str, start: float, end: float) -> Beat:
    return Beat(id=beat_id, start=start, end=end, shots=[(start, end)], keyframe=f"keyframes/{beat_id}.jpg")


BEATS = [beat("B0001", 0, 10), beat("B0002", 10, 20), beat("B0003", 20, 30)]
# pan on screen a lot, hiro in the middle, the mother once at the end, an extra twice
FACES = (
    [face("pan", t) for t in range(0, 25)]
    + [face("hiro", t) for t in range(12, 19)]
    + [face("mother", t) for t in (24, 25, 26)]
    + [face("extra", t) for t in (5, 6)]
)


def test_faces_of_the_same_person_end_up_together_biggest_group_first() -> None:
    groups = cluster_faces(FACES)

    sizes = [len(g) for g in groups]
    assert sizes == sorted(sizes, reverse=True) and sizes[:3] == [25, 7, 3]
    assert {FACES[i].time for i in groups[1]} == set(range(12, 19))


def test_people_seen_too_rarely_are_not_offered_and_ids_follow_screen_time() -> None:
    characters = build_characters(FACES, cluster_faces(FACES), BEATS, interval=1.0)

    assert [c.id for c in characters] == ["C01", "C02", "C03"]  # the extra (2 faces) is left out
    pan, hiro, mother = characters
    assert pan.screen_sec == 25 and pan.beats == ("B0001", "B0002", "B0003")
    assert hiro.beats == ("B0002",) and mother.beats == ("B0003",)


def test_no_faces_means_no_characters() -> None:
    assert build_characters([], cluster_faces([]), BEATS, interval=1.0) == []


def characters():  # noqa: ANN201
    return build_characters(FACES, cluster_faces(FACES), BEATS, interval=1.0)


def test_each_beat_lists_the_names_of_who_is_in_it() -> None:
    choices = Choices(confirmed=True, names={"C01": "Pan", "C02": "Hiro", "C03": "người mẹ"})

    cast = cast_by_beat(characters(), choices)

    assert cast == {"B0001": ("Pan",), "B0002": ("Pan", "Hiro"), "B0003": ("Pan", "người mẹ")}


def test_merged_people_take_the_name_of_who_they_were_merged_into_and_ignored_ones_vanish() -> None:
    # Hiro's group was a second group of Pan (lighting); the mother is an extra to the user
    choices = Choices(confirmed=True, names={"C01": "Pan"}, merged={"C02": "C01"}, ignored=frozenset({"C03"}))

    assert cast_by_beat(characters(), choices) == {"B0001": ("Pan",), "B0002": ("Pan",), "B0003": ("Pan",)}
    assert cast_list(characters(), choices) == [("Pan", 32.0)]


def test_unnamed_people_are_left_out_of_the_cast() -> None:
    choices = Choices(confirmed=True, names={"C02": "Hiro"})

    assert cast_list(characters(), choices) == [("Hiro", 7.0)]
    assert cast_by_beat(characters(), choices) == {"B0002": ("Hiro",)}


def test_two_groups_given_the_same_name_count_as_one_person() -> None:
    choices = Choices(confirmed=True, names={"C01": "Pan", "C02": "Pan "})

    assert cast_list(characters(), choices) == [("Pan", 32.0)]
    assert cast_by_beat(characters(), choices)["B0002"] == ("Pan",)


def test_choices_are_saved_and_read_back_and_a_damaged_file_means_none(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    assert load_choices(tmp_path) == Choices()

    choices = Choices(confirmed=True, names={"C01": "Pan"}, merged={"C04": "C01"}, ignored=frozenset({"C05"}))
    save_choices(tmp_path, choices)
    assert load_choices(tmp_path) == choices

    (tmp_path / "work/character_choices.json").write_text("{nope", encoding="utf-8")
    assert load_choices(tmp_path) == Choices()


@pytest.mark.parametrize("merged", [{"C02": "C02"}, {"C02": "C03", "C03": "C02"}])
def test_merge_loops_are_ignored(merged: dict[str, str]) -> None:
    choices = Choices(confirmed=True, names={"C02": "Hiro", "C03": "Mẹ"}, merged=merged)

    cast = cast_by_beat(characters(), choices)

    assert cast["B0002"] == ("Hiro",)  # no endless loop, each group keeps its own name


def test_gender_shows_next_to_proper_names_and_merged_people_take_the_target_gender() -> None:
    choices = Choices(confirmed=True, names={"C01": "Pan", "C02": "Hiro", "C03": "người mẹ"}, genders={"C01": "nữ", "C02": "nam"})

    assert cast_by_beat(characters(), choices) == {
        "B0001": ("Pan (nữ)",), "B0002": ("Pan (nữ)", "Hiro (nam)"), "B0003": ("Pan (nữ)", "người mẹ"),
    }  # fmt: skip
    merged = Choices(confirmed=True, names={"C01": "Pan"}, merged={"C02": "C01"}, genders={"C01": "nữ", "C02": "nam"})
    assert cast_by_beat(characters(), merged)["B0002"] == ("Pan (nữ)",)
    assert cast_list(characters(), merged)[0][0] == "Pan (nữ)"


def test_genders_are_saved_and_read_back(tmp_path: Path) -> None:
    choices = Choices(confirmed=True, names={"C01": "Pan"}, genders={"C01": "nữ"})
    (tmp_path / "work").mkdir()

    save_choices(tmp_path, choices)

    assert load_choices(tmp_path) == choices
