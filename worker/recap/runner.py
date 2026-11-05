"""Runs one stage: dependency check, skip-if-valid, state bookkeeping, error/cancel handling."""

from __future__ import annotations

import signal
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import IO, Protocol

from recap import config
from recap.events import Emitter
from recap.models import STAGE_NAMES, Project, StageError, StageState, StageStatus, now_iso


class StageFailure(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Cancelled(BaseException):
    """Raised from the SIGTERM/SIGINT handler. BaseException so stage code can't swallow it."""


@dataclass(frozen=True)
class StageContext:
    project_dir: Path
    project: Project
    emitter: Emitter
    log: IO[str]
    force: bool = False  # user asked to redo the stage: do not reuse partial results

    def path(self, rel: str) -> Path:
        return self.project_dir / rel


class Stage(Protocol):
    name: str

    def outputs(self, project: Project) -> list[str]: ...

    def fingerprint(self, project_dir: Path, project: Project) -> str: ...

    def run(self, ctx: StageContext) -> Project: ...


def install_signal_handlers() -> None:
    def handler(signum: int, _frame: FrameType | None) -> None:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        raise Cancelled(signal.Signals(signum).name)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


# Stages the next one can do without: `inspect` only adds skips and cleanup, `characters` only
# names (and projects made before them have them pending), so `describe` needs `scenes`.
OPTIONAL_STAGES = frozenset({"inspect", "characters"})


def check_dependency(project: Project, name: str) -> None:
    index = STAGE_NAMES.index(name)
    earlier = [s for s in STAGE_NAMES[:index] if s not in OPTIONAL_STAGES]
    if not earlier:
        return
    previous = earlier[-1]
    # STALE: outputs exist but are outdated; running on them is allowed (the user decides)
    if project.stages[previous].status not in (StageStatus.DONE, StageStatus.STALE):
        raise StageFailure("dependency_not_done", f"Stage '{previous}' phải chạy xong trước '{name}'")


def is_up_to_date(project_dir: Path, project: Project, stage: Stage) -> bool:
    state = project.stages[stage.name]
    if state.status != StageStatus.DONE or state.fingerprint is None:
        return False
    outputs_exist = all((project_dir / rel).exists() for rel in stage.outputs(project))
    return outputs_exist and state.fingerprint == stage.fingerprint(project_dir, project)


def run_stage(project_dir: Path, name: str, stages: dict[str, Stage], emitter: Emitter, force: bool = False) -> bool:
    """Run one stage. Returns True on success (or valid skip). Re-raises Cancelled after recording it."""
    try:
        project = config.load_project(project_dir)
        check_dependency(project, name)
        stage = stages.get(name)
        if stage is None:
            raise StageFailure("not_implemented", f"Stage '{name}' chưa được triển khai")
    except config.ProjectError as exc:
        emitter.error(name, exc.code, exc.message, retryable=False)
        return False
    except StageFailure as exc:
        emitter.error(name, exc.code, exc.message, exc.retryable)
        return False

    with emitter.stage_log(project_dir / "logs" / f"{name}.log") as log:
        return _execute(project_dir, project, stage, emitter, log, force)


def _execute(project_dir: Path, project: Project, stage: Stage, emitter: Emitter, log: IO[str], force: bool) -> bool:
    name = stage.name
    started = time.monotonic()
    try:
        if not force and is_up_to_date(project_dir, project, stage):
            emitter.raw(f"{name}: kết quả còn hợp lệ, bỏ qua")
            emitter.stage_end(name, StageStatus.DONE.value, elapsed_sec=0.0, skipped=True)
            return True
        running = StageState(status=StageStatus.RUNNING, progress=0.0, started_at=now_iso())
        config.save_project(project_dir, project.with_stage(name, running))
        emitter.stage_start(name)
        updated = stage.run(StageContext(project_dir, project, emitter, log, force))
        elapsed = round(time.monotonic() - started, 2)
        done = running.model_copy(
            update={
                "status": StageStatus.DONE,
                "progress": 1.0,
                "finished_at": now_iso(),
                "elapsed_sec": elapsed,
                "fingerprint": stage.fingerprint(project_dir, updated),
            }
        )
        config.save_project(project_dir, updated.with_stage(name, done))
        emitter.stage_end(name, StageStatus.DONE.value, elapsed_sec=elapsed)
        return True
    except StageFailure as exc:
        error = StageError(code=exc.code, message=exc.message, retryable=exc.retryable)
        _record_failure(project_dir, project, name, started, error, emitter)
        return False
    except Cancelled:
        error = StageError(code="cancelled", message="Đã hủy", retryable=True)
        _record_failure(project_dir, project, name, started, error, emitter)
        raise
    except Exception as exc:  # unexpected bug: keep the traceback in the stage log
        emitter.raw(traceback.format_exc())
        error = StageError(code="internal", message=f"{type(exc).__name__}: {exc}", retryable=True)
        _record_failure(project_dir, project, name, started, error, emitter)
        return False


def _record_failure(
    project_dir: Path, project: Project, name: str, started: float, error: StageError, emitter: Emitter
) -> None:
    state = StageState(
        status=StageStatus.FAILED,
        started_at=project.stages[name].started_at or now_iso(),
        finished_at=now_iso(),
        elapsed_sec=round(time.monotonic() - started, 2),
        error=error,
    )
    config.save_project(project_dir, project.with_stage(name, state))
    emitter.error(name, error.code, error.message, error.retryable)
    emitter.stage_end(name, StageStatus.FAILED.value, elapsed_sec=state.elapsed_sec or 0.0)
