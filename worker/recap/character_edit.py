"""Saving the user's naming from the app's "Nhân vật" tab (`recap characters-save`).

Like the other edits, it goes through the worker (which checks it against work/characters.json)
and marks the stages it changes as stale: a new naming means a new script.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from recap import config
from recap.characters import GENDERS, Choices, load_characters, save_choices
from recap.runner import Stage
from recap.script_edit import ScriptEditError
from recap.settings_update import save_marking_stale

MAX_NAME = 60


def _invalid(message: str) -> ScriptEditError:
    return ScriptEditError("naming_invalid", message)


def _mapping(raw: Any, key: str) -> dict[str, str]:
    value = raw.get(key, {})
    if not isinstance(value, Mapping) or not all(isinstance(v, str) for v in value.values()):
        raise _invalid(f"“{key}” phải là bảng mã nhân vật → chữ")
    return {str(k): v for k, v in value.items()}


def _check_ids(ids: set[str], known: set[str], what: str) -> None:
    unknown = sorted(ids - known)
    if unknown:
        raise _invalid(f"{what}: không có nhân vật {', '.join(unknown)}")


def _clean_names(names: dict[str, str]) -> dict[str, str]:
    cleaned = {}
    for character, name in names.items():
        text = name.strip()
        if "\n" in text or len(text) > MAX_NAME:
            raise _invalid(f"Tên của {character} phải là một dòng, tối đa {MAX_NAME} ký tự")
        if text:
            cleaned[character] = text
    return cleaned


def parse_naming(raw: Any, known: set[str]) -> Choices:
    if not isinstance(raw, Mapping):
        raise _invalid("Lựa chọn phải là một bảng JSON")
    if raw.get("skip"):
        return Choices(confirmed=True)
    names, merged = _clean_names(_mapping(raw, "names")), _mapping(raw, "merged")
    ignored = raw.get("ignored", [])
    if not isinstance(ignored, list) or not all(isinstance(i, str) for i in ignored):
        raise _invalid("“ignored” phải là danh sách mã nhân vật")
    _check_ids(set(names), known, "Tên")
    _check_ids(set(merged) | set(merged.values()), known, "Gộp")
    _check_ids(set(ignored), known, "Bỏ qua")
    genders = {k: v for k, v in _mapping(raw, "genders").items() if v}
    _check_ids(set(genders), known, "Giới tính")
    if any(v not in GENDERS for v in genders.values()):
        raise _invalid("Giới tính chỉ là “nam” hoặc “nữ”")
    if any(source == target for source, target in merged.items()):
        raise _invalid("Không gộp một nhân vật vào chính nó")
    return Choices(confirmed=True, names=names, merged=merged, ignored=frozenset(ignored), genders=genders)


def save_naming(project_dir: Path, raw: Any, stages: Mapping[str, Stage]) -> list[str]:
    """Check and save the naming (it counts as confirmed); returns the stages marked stale."""
    characters = load_characters(project_dir)
    if not characters:
        raise ScriptEditError("input_missing", "Chưa có nhân vật để đặt tên: hãy chạy bước Nhân vật trước")
    choices = parse_naming(raw, {c.id for c in characters})
    save_choices(project_dir, choices)
    return save_marking_stale(project_dir, config.load_project(project_dir), stages)
