"""The user's changes to the AI music plan (from the app): pick an option, edit or redo a cue.

Both rewrite work/music/plan.json and mark the music stage and everything after it stale; the
next run of the music stage then generates only what changed (see `music_options`).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from recap import config
from recap.music_plan import prompt_problems
from recap.runner import Stage
from recap.settings_update import save_marking_stale
from recap.stages.music import PLAN, load_plan, project_banned_terms


class MusicEditError(Exception):
    pass


def _plan_and_cue(project_dir: Path, cue_id: str) -> tuple[dict[str, Any], int]:
    plan = load_plan(project_dir)
    if plan is None:
        raise MusicEditError("Chưa có nhạc AI, hãy chạy stage music trước")
    index = next((i for i, c in enumerate(plan.get("cues", [])) if c.get("id") == cue_id), None)
    if index is None:
        raise MusicEditError(f"Không có phần nhạc {cue_id}")
    return plan, index


def _save(project_dir: Path, plan: dict[str, Any], index: int, cue: dict[str, Any], stages: Mapping[str, Stage]) -> list[str]:
    cues = [cue if i == index else c for i, c in enumerate(plan["cues"])]
    config.write_json_atomic(project_dir / PLAN, {**plan, "cues": cues})
    return save_marking_stale(project_dir, config.load_project(project_dir), stages)


def choose_option(project_dir: Path, cue_id: str, option: int, stages: Mapping[str, Stage]) -> list[str]:
    """Use option `option` (0-based) of `cue_id`. Returns the stages marked stale."""
    plan, index = _plan_and_cue(project_dir, cue_id)
    cue = plan["cues"][index]
    count = len(cue.get("options", []))
    if not 0 <= option < count:
        raise MusicEditError(f"{cue_id} chỉ có {count} bản nhạc (chọn 0..{count - 1})")
    return _save(project_dir, plan, index, {**cue, "chosen": option}, stages)


def edit_cue(
    project_dir: Path,
    cue_id: str,
    stages: Mapping[str, Stage],
    prompt: str | None = None,
    alt_prompt: str | None = None,
    reroll: bool = False,
) -> list[str]:
    """New prompt(s) and/or new seeds ("reroll") for one cue. Returns the stages marked stale."""
    plan, index = _plan_and_cue(project_dir, cue_id)
    cue = dict(plan["cues"][index])
    banned = project_banned_terms(project_dir, config.load_project(project_dir).title)
    for field, value in (("prompt", prompt), ("alt_prompt", alt_prompt)):
        if value is None:
            continue
        problems = prompt_problems(value.strip(), banned)
        if problems:
            raise MusicEditError(f"{field}: {'; '.join(problems)}")
        cue[field] = value.strip()
    if reroll:
        cue["round"] = int(cue.get("round", 0)) + 1
    return _save(project_dir, plan, index, cue, stages)
