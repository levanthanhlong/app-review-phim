"""Music generators (Stable Audio 3 MLX, ACE-Step 1.5) run as subprocesses from their own venvs.

Each lives in its own folder under the repo's tools/ (own venv: their MLX/torch pins must not
clash with the worker's). Paths can be overridden with RECAP_SA3_DIR / RECAP_ACESTEP_DIR.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

from recap.config import REPO_ROOT

INSTRUMENTAL = ", instrumental, no vocals"
GEN_EXTRA_SEC = 3.0  # covers the crossfade into the next cue
MIN_GEN_SEC = 10.0
MAX_GEN_SEC = 180.0  # longer cues loop the track (with crossfades)
OPTION_LABELS = ("A", "B", "C")
SEED_RANGE = 2**31
SA3_CFG = 1.0  # the released ARC checkpoints are distilled for a single pass (no negative prompt)
SA3_DECODERS = {"medium": "same-l", "sm-music": "same-s"}
ACE_CONFIG = "acestep-v15-turbo"
TERMINATE_TIMEOUT_SEC = 10

SA3_DIR = Path(os.environ.get("RECAP_SA3_DIR", str(REPO_ROOT / "tools/stable-audio-3/optimized/mlx")))
ACE_DIR = Path(os.environ.get("RECAP_ACESTEP_DIR", str(REPO_ROOT / "tools/ACE-Step-1.5")))
ACE_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "ace_generate.py"
SA3_BATCH_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "sa3_batch.py"
SA3_JOBS_FILE = "sa3_jobs.json"  # written next to the outputs


class MusicGenError(Exception):
    pass


@dataclass(frozen=True)
class OptionSpec:
    label: str
    prompt: str  # as written in the plan (without INSTRUMENTAL)
    seed: int


@dataclass(frozen=True)
class GenRequest:
    prompt: str  # sent to the model
    seconds: float
    seed: int
    model: str  # settings.music_model


class Generator(Protocol):
    engine: str

    def available(self) -> bool: ...

    def model_name(self, req: GenRequest) -> str: ...

    def generate(self, req: GenRequest, out: Path, log: IO[str]) -> None: ...

    def provenance(self) -> dict[str, Any]: ...


def _seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return int(digest[:8], 16) % SEED_RANGE


def option_specs(cue_id: str, prompt: str, alt_prompt: str, round_: int) -> list[OptionSpec]:
    """A and B: the main prompt with two seeds; C: the alternative prompt. `round_` > 0 = "make new ones"."""
    prompts = (prompt, prompt, alt_prompt)
    return [OptionSpec(label, p, _seed(cue_id, label, round_)) for label, p in zip(OPTION_LABELS, prompts)]


def gen_seconds(cue_sec: float) -> float:
    return min(MAX_GEN_SEC, max(MIN_GEN_SEC, round(cue_sec + GEN_EXTRA_SEC, 1)))


def _git_commit(folder: Path) -> str:
    proc = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def run_tool(cmd: list[str], cwd: Path, log: IO[str]) -> None:
    """Run a generator CLI (output to the stage log); killed if the stage is cancelled."""
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    try:
        code = proc.wait()
    except BaseException:
        proc.terminate()
        try:
            proc.wait(timeout=TERMINATE_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    if code != 0:
        raise MusicGenError(f"{Path(cmd[0]).name} lỗi (exit {code}), xem logs/music.log")


def run_tool_lines(cmd: list[str], cwd: Path, log: IO[str], on_line: Callable[[str], None]) -> None:
    """Like `run_tool`, but stdout lines go to `on_line` (stderr to the log)."""
    log.write("$ " + " ".join(cmd) + "\n")
    log.flush()
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line.strip())
        code = proc.wait()
    except BaseException:
        proc.terminate()
        try:
            proc.wait(timeout=TERMINATE_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    if code != 0:
        raise MusicGenError(f"{Path(cmd[1]).name} lỗi (exit {code}), xem logs/music.log")


def _check_output(out: Path) -> None:
    if not out.is_file() or out.stat().st_size == 0:
        raise MusicGenError(f"Không tạo được file nhạc {out.name}")


class SA3Generator:
    """Stable Audio 3, Stability's pure-MLX runtime (`sa3` CLI)."""

    engine = "stable-audio-3"

    def __init__(self, folder: Path = SA3_DIR, batch_script: Path = SA3_BATCH_SCRIPT) -> None:
        self._folder = folder
        self._batch_script = batch_script

    def available(self) -> bool:
        return (self._folder / "sa3").is_file() and (self._folder / ".venv").is_dir()

    def model_name(self, req: GenRequest) -> str:
        return f"stabilityai/stable-audio-3-optimized dit={req.model} decoder={SA3_DECODERS[req.model]}"

    def command(self, req: GenRequest, out: Path) -> list[str]:
        # absolute --out: sa3 puts a relative path under its own output/ folder
        return [
            str(self._folder / "sa3"), "--prompt", req.prompt,
            "--dit", req.model, "--decoder", SA3_DECODERS[req.model],
            "--seconds", f"{req.seconds:g}", "--seed", str(req.seed), "--cfg", f"{SA3_CFG:g}",
            "--out", str(out.resolve()),
        ]  # fmt: skip

    def generate(self, req: GenRequest, out: Path, log: IO[str]) -> None:
        run_tool(self.command(req, out), self._folder, log)
        _check_output(out)

    def batch_command(self, jobs_file: Path) -> list[str]:
        python = self._folder / ".venv" / "bin" / "python"
        return [str(python), str(self._batch_script), "--sa3", str(self._folder), "--jobs", str(jobs_file.resolve())]

    def generate_batch(self, items: list[tuple[GenRequest, Path]], log: IO[str], on_done: Callable[[Path], None]) -> None:
        """All items in one process (models loaded once); `on_done(out)` as each file is written."""
        if not items:
            return
        models = {req.model for req, _ in items}
        if len(models) != 1:
            raise MusicGenError(f"Một lần chạy chỉ dùng một model (đang có {sorted(models)})")
        by_path = {str(out.resolve()): out for _, out in items}
        jobs = [{"prompt": r.prompt, "seconds": r.seconds, "seed": r.seed, "out": str(o.resolve())} for r, o in items]
        jobs_file = items[0][1].parent / SA3_JOBS_FILE
        jobs_file.write_text(json.dumps({"model": models.pop(), "jobs": jobs}, ensure_ascii=False), encoding="utf-8")

        def line(text: str) -> None:
            try:
                out = by_path[json.loads(text)["out"]]
            except (json.JSONDecodeError, KeyError, TypeError):
                log.write(text + "\n")  # not a progress line
                return
            _check_output(out)
            on_done(out)

        try:
            run_tool_lines(self.batch_command(jobs_file), self._folder, log, line)
        finally:
            jobs_file.unlink(missing_ok=True)

    def provenance(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "code": f"https://github.com/Stability-AI/stable-audio-3 (optimized/mlx) @ {_git_commit(self._folder)}",
            "weights": "https://huggingface.co/stabilityai/stable-audio-3-optimized",
            "license": "Stability AI Community License",
            "license_url": "https://stability.ai/license",
            "license_note": "Dùng thương mại được khi doanh thu năm dưới 1 triệu USD; người dùng sở hữu output.",
            "cfg": SA3_CFG,
        }


class AceStepGenerator:
    """ACE-Step 1.5 (fallback), through tools/ace_generate.py run with ACE-Step's own Python."""

    engine = "ace-step-1.5"

    def __init__(self, folder: Path = ACE_DIR, script: Path = ACE_SCRIPT) -> None:
        self._folder = folder
        self._script = script

    def _python(self) -> Path:
        return self._folder / ".venv" / "bin" / "python"

    def available(self) -> bool:
        return self._python().is_file() and self._script.is_file()

    def model_name(self, req: GenRequest) -> str:
        return f"ACE-Step 1.5 {ACE_CONFIG}"

    def command(self, req: GenRequest, out: Path) -> list[str]:
        return [
            str(self._python()), str(self._script), "--project-root", str(self._folder),
            "--config", ACE_CONFIG, "--caption", req.prompt,
            "--seconds", f"{req.seconds:g}", "--seed", str(req.seed), "--out", str(out.resolve()),
        ]  # fmt: skip

    def generate(self, req: GenRequest, out: Path, log: IO[str]) -> None:
        run_tool(self.command(req, out), self._folder, log)
        _check_output(out)

    def provenance(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "code": f"https://github.com/ace-step/ACE-Step-1.5 @ {_git_commit(self._folder)}",
            "weights": "https://huggingface.co/ACE-Step/Ace-Step1.5",
            "license": "MIT (mã nguồn)",
            "license_url": "https://github.com/ace-step/ACE-Step-1.5/blob/main/LICENSE",
            "license_note": "Repo không nêu riêng điều khoản cho trọng số model và nhạc tạo ra.",
        }


def default_generators() -> list[Generator]:
    return [SA3Generator(), AceStepGenerator()]
