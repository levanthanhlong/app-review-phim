"""Music stage, generation part: 3 options per cue, cache, fallback engine, provenance, user edits."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import pytest

from recap import config
from recap.events import Emitter
from recap.models import StageStatus
from recap.music_edit import MusicEditError, choose_option, edit_cue
from recap.music_gen import (
    INSTRUMENTAL,
    AceStepGenerator,
    GenRequest,
    MusicGenError,
    SA3Generator,
    gen_seconds,
    option_specs,
)
from recap.runner import run_stage
from recap.stages import STAGES
from recap.stages.music import PLAN, PROVENANCE, MusicStage
from tests.test_cli import run_cli
from tests.test_music_stage import GOOD, make_project
from tests.test_script_stage import FakeLLM


class FakeGenerator:
    def __init__(self, engine: str, fail: bool = False, available: bool = True) -> None:
        self.engine = engine
        self.fail = fail
        self._available = available
        self.requests: list[GenRequest] = []

    def available(self) -> bool:
        return self._available

    def model_name(self, req: GenRequest) -> str:
        return f"{self.engine}-{req.model}"

    def generate(self, req: GenRequest, out: Path, log: IO[str]) -> None:
        self.requests.append(req)
        if self.fail:
            raise MusicGenError(f"{self.engine} hỏng")
        out.write_bytes(f"{req.prompt}|{req.seed}".encode())

    def provenance(self) -> dict[str, Any]:
        return {"engine": self.engine, "license": f"{self.engine} license"}


def run(project_dir: Path, generators: list[FakeGenerator], llm: FakeLLM | None = None) -> tuple[bool, list[dict]]:
    out = io.StringIO()
    stage = MusicStage(provider_factory=lambda settings: llm or FakeLLM([GOOD]), generators=generators)
    ok = run_stage(project_dir, "music", {"music": stage}, Emitter(stream=out, raw_stream=io.StringIO()))
    return ok, [json.loads(line) for line in out.getvalue().splitlines()]


def plan(project_dir: Path) -> dict:
    return json.loads((project_dir / PLAN).read_text())


# ---- pure helpers ---------------------------------------------------------------------------


def test_three_options_two_seeds_of_the_prompt_and_one_of_the_alternative() -> None:
    specs = option_specs("M01", "dark strings", "sparse piano", round_=0)

    assert [s.label for s in specs] == ["A", "B", "C"]
    assert [s.prompt for s in specs] == ["dark strings", "dark strings", "sparse piano"]
    assert len({specs[0].seed, specs[1].seed}) == 2
    assert option_specs("M01", "x", "y", round_=0) == option_specs("M01", "x", "y", round_=0)  # reproducible
    assert option_specs("M01", "x", "y", round_=1)[0].seed != specs[0].seed  # "make new ones" changes seeds


@pytest.mark.parametrize(("cue_sec", "expected"), [(5.0, 10.0), (51.5, 54.5), (400.0, 180.0)])
def test_generated_length_covers_the_cue_within_limits(cue_sec: float, expected: float) -> None:
    assert gen_seconds(cue_sec) == expected


# ---- generation ---------------------------------------------------------------------------


def test_generates_three_options_per_cue_and_records_provenance(tmp_path: Path) -> None:
    # Arrange
    project_dir = make_project(tmp_path)
    sa3 = FakeGenerator("sa3")

    # Act
    ok, _ = run(project_dir, [sa3, FakeGenerator("ace")])

    # Assert
    assert ok and len(sa3.requests) == 3
    assert all(r.prompt.endswith(INSTRUMENTAL) and r.model == "medium" for r in sa3.requests)
    options = plan(project_dir)["cues"][0]["options"]
    assert [o["label"] for o in options] == ["A", "B", "C"]
    assert all((project_dir / "work" / o["file"]).is_file() and o["engine"] == "sa3" for o in options)
    proof = json.loads((project_dir / PROVENANCE).read_text())
    chosen = proof["cues"][0]
    assert chosen["chosen"]["label"] == "A" and chosen["chosen"]["sha256"] and chosen["chosen"]["seed"]
    assert proof["engines"]["sa3"]["license"] == "sa3 license"


def test_second_run_reuses_plan_and_audio(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    run(project_dir, [FakeGenerator("sa3")])

    sa3, llm = FakeGenerator("sa3"), FakeLLM([])
    ok, events = run(project_dir, [sa3], llm)

    assert ok and events[-1]["skipped"] is True and sa3.requests == [] and llm.prompts == []


def test_falls_back_to_the_next_engine_when_the_first_fails(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    ace = FakeGenerator("ace")

    ok, events = run(project_dir, [FakeGenerator("sa3", fail=True), ace])

    assert ok and len(ace.requests) == 3
    assert {o["engine"] for o in plan(project_dir)["cues"][0]["options"]} == {"ace"}
    assert any("sa3 hỏng" in e.get("message", "") for e in events if e["type"] == "log")


def test_no_engine_installed_is_a_clear_error(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)

    ok, events = run(project_dir, [FakeGenerator("sa3", available=False)])

    assert not ok and [e["code"] for e in events if e["type"] == "error"] == ["music_engine_missing"]


def test_every_engine_failing_stops_the_stage(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)

    ok, events = run(project_dir, [FakeGenerator("sa3", fail=True), FakeGenerator("ace", fail=True)])

    assert not ok and [e["code"] for e in events if e["type"] == "error"] == ["music_failed"]


# ---- choices and edits from the app --------------------------------------------------------


def done_music_project(tmp_path: Path) -> Path:
    project_dir = make_project(tmp_path)
    assert run(project_dir, [FakeGenerator("sa3")])[0]
    project = config.load_project(project_dir)
    # earlier stages carry their real fingerprints (unchanged inputs); assemble/export are done too
    done = {n: s.model_copy(update={"status": StageStatus.DONE,
                                    "fingerprint": s.fingerprint or STAGES[n].fingerprint(project_dir, project)})
            for n, s in project.stages.items()}  # fmt: skip
    config.save_project(project_dir, project.model_copy(update={"stages": done}))
    return project_dir


def test_choosing_another_option_marks_music_and_later_stages_stale(tmp_path: Path) -> None:
    project_dir = done_music_project(tmp_path)

    stale = choose_option(project_dir, "M01", 2, STAGES)

    assert plan(project_dir)["cues"][0]["chosen"] == 2
    assert stale == ["music", "assemble", "export"]


def test_choosing_a_missing_option_is_rejected(tmp_path: Path) -> None:
    project_dir = done_music_project(tmp_path)

    with pytest.raises(MusicEditError):
        choose_option(project_dir, "M01", 3, STAGES)
    with pytest.raises(MusicEditError):
        choose_option(project_dir, "M09", 0, STAGES)


def test_editing_a_prompt_regenerates_only_that_cue(tmp_path: Path) -> None:
    # Arrange
    project_dir = done_music_project(tmp_path)

    # Act
    edit_cue(project_dir, "M01", STAGES, prompt="warm acoustic guitar, 90 bpm, hopeful")
    sa3 = FakeGenerator("sa3")
    ok, _ = run(project_dir, [sa3], FakeLLM([]))  # no new LLM plan: the edited one is kept

    # Assert: A and B use the new prompt, C (alternative prompt) is reused from the cache
    assert ok and [r.prompt for r in sa3.requests] == [f"warm acoustic guitar, 90 bpm, hopeful{INSTRUMENTAL}"] * 2
    assert plan(project_dir)["cues"][0]["prompt"] == "warm acoustic guitar, 90 bpm, hopeful"


def test_reroll_makes_three_new_options(tmp_path: Path) -> None:
    project_dir = done_music_project(tmp_path)
    before = [o["seed"] for o in plan(project_dir)["cues"][0]["options"]]

    edit_cue(project_dir, "M01", STAGES, reroll=True)
    sa3 = FakeGenerator("sa3")
    run(project_dir, [sa3], FakeLLM([]))

    assert len(sa3.requests) == 3
    assert not set(before) & {o["seed"] for o in plan(project_dir)["cues"][0]["options"]}


def test_editing_with_a_character_name_is_rejected(tmp_path: Path) -> None:
    project_dir = done_music_project(tmp_path)

    with pytest.raises(MusicEditError, match="toad"):
        edit_cue(project_dir, "M01", STAGES, alt_prompt="theme for Toad")


def test_cli_choose_and_edit_report_stale_stages(tmp_path: Path) -> None:
    project_dir = done_music_project(tmp_path)

    chose = run_cli("music-choose", "--project", str(project_dir), "--cue", "M01", "--option", "1")
    edited = run_cli("music-edit", "--project", str(project_dir), "--cue", "M01", "--prompt", "calm harp, 70 bpm")
    bad = run_cli("music-edit", "--project", str(project_dir), "--cue", "M01", "--prompt", "Toad march")

    assert chose[0] == 0 and chose[1][-1]["data"]["stale"] == ["music", "assemble", "export"]
    assert edited[0] == 0 and plan(project_dir)["cues"][0]["prompt"] == "calm harp, 70 bpm"
    assert bad[0] == 1 and bad[1][-1]["code"] == "music_edit_invalid"


@pytest.mark.parametrize("generator", [SA3Generator(Path("/sa3")), AceStepGenerator(Path("/ace"))])
def test_generators_get_an_absolute_output_path(generator: SA3Generator | AceStepGenerator) -> None:
    # sa3 puts a relative --out under its own output/ folder: the file would be lost
    req = GenRequest("calm harp", 20.0, 7, "medium")

    cmd = generator.command(req, Path("rel/out.wav"))

    out = cmd[cmd.index("--out") + 1]
    assert Path(out).is_absolute() and out.endswith("rel/out.wav")


class FakeBatchGenerator(FakeGenerator):
    """Generates all requests in one call, like the Stable Audio 3 batch runner; can stop after `stop_after`."""

    def __init__(self, engine: str, stop_after: int | None = None) -> None:
        super().__init__(engine)
        self.batches: list[int] = []
        self.stop_after = stop_after

    def generate_batch(self, items: list[tuple[GenRequest, Path]], log: IO[str], on_done: Callable[[Path], None]) -> None:
        self.batches.append(len(items))
        for n, (req, out) in enumerate(items):
            if self.stop_after is not None and n == self.stop_after:
                raise MusicGenError("batch dừng giữa chừng")
            self.generate(req, out, log)
            on_done(out)


def test_all_missing_options_go_to_the_engine_in_one_batch(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    sa3 = FakeBatchGenerator("sa3")

    ok, _ = run(project_dir, [sa3])

    assert ok and sa3.batches == [3]


def test_options_left_by_a_failed_batch_fall_back_and_finished_ones_are_kept(tmp_path: Path) -> None:
    project_dir = make_project(tmp_path)
    sa3, ace = FakeBatchGenerator("sa3", stop_after=1), FakeGenerator("ace")

    ok, _ = run(project_dir, [sa3, ace])

    assert ok and len(ace.requests) == 2
    assert [o["engine"] for o in plan(project_dir)["cues"][0]["options"]] == ["sa3", "ace", "ace"]


@pytest.mark.integration
@pytest.mark.skipif(not SA3Generator().available(), reason="Stable Audio 3 not installed in tools/")
def test_real_batch_matches_the_cli_bit_for_bit(tmp_path: Path) -> None:
    # Arrange
    gen = SA3Generator()
    req = GenRequest(f"soft felt piano, 70 bpm{INSTRUMENTAL}", 3.0, 11, "sm-music")
    finished: list[Path] = []

    # Act (the stage log is a real file: the tools write to its descriptor)
    with (tmp_path / "music.log").open("w") as log:
        gen.generate(req, tmp_path / "cli.wav", log)
        gen.generate_batch([(req, tmp_path / "batch.wav")], log, finished.append)

    # Assert: same seed, same audio (cached files and provenance stay valid)
    assert finished == [tmp_path / "batch.wav"]
    assert (tmp_path / "batch.wav").read_bytes() == (tmp_path / "cli.wav").read_bytes()
