"""`recap set`: change project settings (validated) and mark the stages they invalidate as stale.

The app never writes project.json itself: the worker owns the schema, so every change goes
through here. A DONE stage whose fingerprint changes becomes STALE, and so does every DONE
stage after it (its inputs are about to change).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from recap import config
from recap.branding import BrandingError, check_image, load_style
from recap.models import STAGE_NAMES, Project, Settings, StageStatus
from recap.music import MusicError, check_music_file
from recap.runner import Stage, StageFailure
from recap.tts_engines import TTSError, rate_factor
from recap.voices import check_voice


class SettingsError(Exception):
    pass


def _parse_value(raw: str) -> Any:
    """JSON scalars (0.25, 1, null, true) become numbers/None/bools; anything else stays text."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return value if value is None or isinstance(value, (str, int, float, bool)) else raw


def parse_assignments(assignments: Iterable[str]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for text in assignments:
        key, sep, raw = text.partition("=")
        key = key.strip()
        if not sep or not key:
            raise SettingsError(f"Sai cú pháp {text!r}: cần dạng khóa=giá_trị")
        if key not in Settings.model_fields:
            raise SettingsError(f"Không có setting {key!r}. Các setting: {', '.join(Settings.model_fields)}")
        updates[key] = _parse_value(raw.strip())
    return updates


def _validated(current: Settings, updates: Mapping[str, Any]) -> Settings:
    # empty = none (no music file / no branding)
    updates = {k: (None if k in ("music_file", "brand_banner", "brand_logo") and v == "" else v) for k, v in updates.items()}
    try:
        settings = Settings.model_validate({**current.model_dump(), **updates})
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise SettingsError(problems) from exc
    try:
        check_voice(settings.tts_engine, settings.voice)
        rate_factor(settings.tts_rate)
        if settings.music_file:
            check_music_file(settings.music_file)
        for image in (settings.brand_banner, settings.brand_logo):
            if image:
                check_image(image)
        if settings.brand_banner:
            load_style(check_image(settings.brand_banner))  # a broken brand.json is reported now
    except (TTSError, MusicError, BrandingError) as exc:
        raise SettingsError(str(exc)) from exc
    return settings


def apply_settings(
    project_dir: Path, updates: Mapping[str, Any], stages: Mapping[str, Stage]
) -> tuple[Settings, list[str]]:
    """Validate and save `updates`. Returns the new settings and the stages marked stale."""
    project = config.load_project(project_dir)
    settings = _validated(project.settings, updates)
    return settings, save_marking_stale(project_dir, project.model_copy(update={"settings": settings}), stages)


def _fingerprint_changed(project_dir: Path, project: Project, stage: Stage | None, saved: str | None) -> bool:
    """Whether the stage's inputs changed. One that cannot tell (e.g. `ingest` once the film was
    deleted: its outputs are still there) counts as unchanged, so other changes can be saved."""
    if stage is None:
        return False
    try:
        return saved != stage.fingerprint(project_dir, project)
    except StageFailure:
        return False


def save_marking_stale(project_dir: Path, project: Project, stages: Mapping[str, Stage]) -> list[str]:
    """Save `project` after an input changed: a DONE stage whose fingerprint changed becomes STALE,
    and so does every DONE stage after it. Returns the stages marked stale."""
    stale: list[str] = []
    new_states = dict(project.stages)
    for name in STAGE_NAMES:
        state = project.stages[name]
        if state.status != StageStatus.DONE:
            continue
        changed = _fingerprint_changed(project_dir, project, stages.get(name), state.fingerprint)
        if stale or changed:
            new_states[name] = state.model_copy(update={"status": StageStatus.STALE})
            stale.append(name)

    config.save_project(project_dir, project.model_copy(update={"stages": new_states}))
    return stale
