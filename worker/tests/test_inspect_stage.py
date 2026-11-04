"""Stage `inspect` on generated footage: a picture of large random blocks (no area stays still, even
scaled down) with a static white box as the channel logo; OCR is faked (the real one: test_ocr.py)."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from recap import config
from recap.events import Emitter
from recap.inspect_rules import TextBox
from recap.models import StageStatus
from recap.ocr import OcrError
from recap.runner import Cancelled, run_stage
from recap.stages.inspect import INSPECT, OCR_CACHE, InspectStage, parse_cropdetect
from tests.conftest import HAS_FFMPEG
from tests.test_cli import run_cli

LOGO = (16, 300, 120, 48)  # x, y, w, h of the white box in the 640x360 picture
DURATION = 20


def make_footage(path: Path, letterbox: bool = False) -> Path:
    x, y, w, h = LOGO
    video = f"noise=alls=100:allf=t+u,scale=640:360:flags=neighbor,drawbox=x={x}:y={y}:w={w}:h={h}:color=white:t=fill"
    if letterbox:
        video += ",pad=640:480:0:60:black"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=gray:size=40x24:rate=25:duration={DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={DURATION}",
        "-vf", video, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ], check=True)  # fmt: skip
    return path


@pytest.fixture(scope="module")
def footage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    return make_footage(tmp_path_factory.mktemp("clips") / "logo.mp4")


class FakeOcr:
    """The watermark's text in every frame; a director credit in the first three seconds."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, image: Path) -> list[TextBox]:
        self.calls += 1
        second = int(image.stem) - 1  # frames are numbered from 1, one per second
        boxes = [TextBox("youtube.com/kenhkhac", 1.0, 0.04, 0.86, 0.15, 0.05)]
        if second <= 2:
            boxes.append(TextBox("Đạo diễn: Nguyễn Văn A", 1.0, 0.3, 0.4, 0.4, 0.06))
        return boxes


def prepared(tmp_path: Path, clip: Path) -> Path:
    """Ingested project with 4 beats of 5 s and `scenes` done."""
    code, events = run_cli("new", "--root", str(tmp_path), "--title", "Logo", "--source", str(clip))
    assert code == 0, events
    project_dir = Path(events[-1]["data"]["project"])
    code, events = run_cli("ingest", "--project", str(project_dir))
    assert code == 0, events
    beats = [{"id": f"B{i:04d}", "start": s, "end": s + 5.0, "shots": [[s, s + 5.0]], "keyframe": f"keyframes/B{i:04d}.jpg"}
             for i, s in enumerate(range(0, DURATION, 5), start=1)]  # fmt: skip
    (project_dir / "work/scenes.json").write_text(json.dumps(beats))
    project = config.load_project(project_dir)
    done = project.stages["scenes"].model_copy(update={"status": StageStatus.DONE})
    config.save_project(project_dir, project.with_stage("transcribe", done).with_stage("scenes", done))
    return project_dir


def inspect(project_dir: Path, ocr: FakeOcr, force: bool = False) -> list[dict]:
    out = io.StringIO()
    ok = run_stage(project_dir, "inspect", {"inspect": InspectStage(ocr=ocr)}, Emitter(stream=out, raw_stream=io.StringIO()),
                   force=force)  # fmt: skip
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert ok, events
    return events


@pytest.mark.integration
def test_finds_the_whole_logo_and_skips_the_credit_beat(tmp_path: Path, footage: Path) -> None:
    # Arrange
    project_dir = prepared(tmp_path, footage)

    # Act
    inspect(project_dir, FakeOcr())

    # Assert: the blurred region covers the whole static box, not only the OCR'd text
    report = json.loads((project_dir / INSPECT).read_text())
    assert len(report["watermarks"]) == 1
    wm = report["watermarks"][0]
    x, y, w, h = LOGO
    assert wm["x"] <= x / 640 and wm["y"] <= y / 360 + 0.01
    assert wm["x"] + wm["w"] >= (x + w) / 640 and wm["y"] + wm["h"] >= (y + h) / 360 - 0.01
    assert wm["w"] < 0.3 and wm["h"] < 0.25  # ...and not much more
    assert "kenhkhac" in wm["text"]
    # the credit at 0–2 s skips the first beat only
    assert list(report["skip_beats"]) == ["B0001"] and "đạo diễn" in report["skip_beats"]["B0001"].lower()
    assert report["letterbox"] is None
    assert report["source"]["quality"] == "low"  # 360p source
    assert not (project_dir / "work/inspect/frames").exists()  # sampled frames are not kept


@pytest.mark.integration
def test_ocr_results_are_reused_when_only_the_beats_change(tmp_path: Path, footage: Path) -> None:
    project_dir = prepared(tmp_path, footage)
    inspect(project_dir, FakeOcr())
    beats = json.loads((project_dir / "work/scenes.json").read_text())
    (project_dir / "work/scenes.json").write_text(json.dumps(beats[:2]))

    ocr = FakeOcr()
    events = inspect(project_dir, ocr)

    assert events[-1]["skipped"] is False and ocr.calls == 0
    assert list(json.loads((project_dir / INSPECT).read_text())["skip_beats"]) == ["B0001"]


@pytest.mark.integration
def test_black_bars_in_the_source_are_found(tmp_path: Path) -> None:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not available")
    project_dir = prepared(tmp_path, make_footage(tmp_path / "bars.mp4", letterbox=True))

    inspect(project_dir, FakeOcr())

    box = json.loads((project_dir / INSPECT).read_text())["letterbox"]
    assert box is not None
    assert box["y"] == pytest.approx(60 / 480, abs=0.02) and box["h"] == pytest.approx(360 / 480, abs=0.02)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("[Parsed_cropdetect_0 @ 0x1] x1:0 ... crop=640:360:0:60\n" * 3 + "crop=640:352:0:64\n", (0.0, 0.125, 1.0, 0.75)),
        ("crop=640:480:0:0\n" * 4, None),  # nothing to crop
        ("crop=636:476:2:2\n" * 4, None),  # a few edge pixels are not black bars
        ("no crop lines at all", None),
        ("crop=640:200:0:20\n" * 4, None),  # bars of 20 and 260 px: a dark scene, not a letterbox
        ("crop=200:480:0:0\n" * 4, None),  # keeps under half of the frame
        ("crop=640:360:0:60\n" * 2 + "".join(f"crop=640:{400 + i * 2}:0:{40 - i}\n" for i in range(4)), None),  # no majority
        ("crop=560:480:40:0\n" * 4, (0.0625, 0.0, 0.875, 1.0)),  # pillarbox
    ],
)
def test_parse_cropdetect(stderr: str, expected: tuple[float, float, float, float] | None) -> None:
    region = parse_cropdetect(stderr, 640, 480)

    assert (None if region is None else (region.x, region.y, region.w, region.h)) == expected


class BrokenOcr(FakeOcr):
    """Vision fails on some frames (all when `failing` is None)."""

    def __init__(self, failing: set[int] | None = None) -> None:
        super().__init__()
        self.failing = failing

    def __call__(self, image: Path) -> list[TextBox]:
        if self.failing is None or int(image.stem) - 1 in self.failing:
            self.calls += 1
            raise OcrError("Vision không đọc được")
        return super().__call__(image)


class CancellingOcr(FakeOcr):
    def __init__(self, at: int) -> None:
        super().__init__()
        self.at = at

    def __call__(self, image: Path) -> list[TextBox]:
        if int(image.stem) - 1 == self.at:
            raise Cancelled()
        return super().__call__(image)


@pytest.mark.integration
def test_ocr_unavailable_still_finds_logos_and_lets_the_pipeline_go_on(tmp_path: Path, footage: Path) -> None:
    project_dir = prepared(tmp_path, footage)

    events = inspect(project_dir, BrokenOcr())

    report = json.loads((project_dir / INSPECT).read_text())
    assert report["skip_beats"] == {} and len(report["watermarks"]) == 1  # the static logo, without text
    assert any(e["type"] == "log" and e["level"] == "warning" and "đọc chữ" in e["message"] for e in events)


@pytest.mark.integration
def test_a_frame_vision_cannot_read_is_left_out(tmp_path: Path, footage: Path) -> None:
    project_dir = prepared(tmp_path, footage)

    inspect(project_dir, BrokenOcr(failing={1}))

    assert list(json.loads((project_dir / INSPECT).read_text())["skip_beats"]) == ["B0001"]


@pytest.mark.integration
def test_ocr_resumes_after_a_cancel(tmp_path: Path, footage: Path) -> None:
    project_dir = prepared(tmp_path, footage)
    out = io.StringIO()
    stage = InspectStage(ocr=CancellingOcr(at=12))
    with pytest.raises(Cancelled):
        run_stage(project_dir, "inspect", {"inspect": stage}, Emitter(stream=out, raw_stream=io.StringIO()))

    ocr = FakeOcr()
    inspect(project_dir, ocr)

    assert ocr.calls == DURATION - 12  # frames 0-11 came from the partial cache


@pytest.mark.integration
def test_a_damaged_ocr_cache_is_read_again(tmp_path: Path, footage: Path) -> None:
    project_dir = prepared(tmp_path, footage)
    inspect(project_dir, FakeOcr())
    cache = project_dir / OCR_CACHE
    data = json.loads(cache.read_text())
    cache.write_text(json.dumps({**data, "frames": [{"when": 0}]}))
    (project_dir / INSPECT).unlink()

    ocr = FakeOcr()
    inspect(project_dir, ocr)

    assert ocr.calls == DURATION


class AdOcr(FakeOcr):
    """Also a betting site in the top-right corner from 10 s to 12 s."""

    def __call__(self, image: Path) -> list[TextBox]:
        boxes = super().__call__(image)
        if 10 <= int(image.stem) - 1 <= 12:
            boxes.append(TextBox("Nhà cái w88.bet", 1.0, 0.75, 0.05, 0.2, 0.04))
        return boxes


@pytest.mark.integration
def test_an_ad_over_the_film_is_blurred_while_it_shows_and_its_beat_kept(tmp_path: Path, footage: Path) -> None:
    project_dir = prepared(tmp_path, footage)

    events = inspect(project_dir, AdOcr())

    report = json.loads((project_dir / INSPECT).read_text())
    assert list(report["skip_beats"]) == ["B0001"]  # B0003 (10-15 s) is kept
    [ad] = report["ad_blurs"]
    assert (ad["start"], ad["end"]) == (9.5, 12.5) and "w88" in ad["text"] and ad["x"] < 0.75
    assert any("quảng cáo" in e.get("message", "").lower() and "làm mờ" in e.get("message", "") for e in events)
