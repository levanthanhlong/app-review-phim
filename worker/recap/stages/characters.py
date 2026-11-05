"""Stage `characters`: the people of the film and the names said in it, for the user to match up.

1. One frame per second of the film (the original when it is still there: faces are bigger than
   in the 480p proxy), scaled to 960 px; usable faces detected and recognised (`faces.py`).
   Faces are cached by video, so new beats only redo the cheap part.
2. Faces grouped into people (`characters.py`); people seen in fewer than 3 frames are extras.
   A few face pictures per person are saved for the app.
3. Names said in the dialogue, found by the LLM (`character_names.py`), cached by transcript.

Writes work/characters.json. The user's naming (work/character_choices.json) is kept while the
faces stay the same, and reset when they change (the character ids would mean other people).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import asdict
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image

from recap import config, media
from recap.character_names import NameCandidate, find_names
from recap.characters import CHOICES_FILE, Character, Choices, Face, build_characters, cluster_faces, save_choices
from recap.faces import FaceError, FaceReader
from recap.llm import LLMError, LLMProvider, get_provider
from recap.models import SCENES_ADAPTER, TRANSCRIPT_ADAPTER, Project, Settings
from recap.runner import StageContext, StageFailure

CHARACTERS = "work/characters.json"
WORK_DIR = "work/characters"
FRAMES_DIR = f"{WORK_DIR}/frames"
PICTURES_DIR = f"{WORK_DIR}/faces"  # relative to the project; pictures are listed relative to work/
FACES_CACHE = f"{WORK_DIR}/faces.npz"
NAMES_CACHE = f"{WORK_DIR}/names.json"
PROXY = "work/proxy_480p.mp4"
SCENES = "work/scenes.json"
TRANSCRIPT = "work/transcript.json"
SAMPLE_FPS = 1.0
FRAME_WIDTH = 960
PICTURE_SIZE = 160
PICTURE_MARGIN = 0.3  # around the face box, so hair and chin show
FACES_VERSION = "1"  # bump when what `faces.py` keeps changes
PROGRESS_EVERY = 20


class Reader(Protocol):
    def read(self, image: Path, time: float) -> list[Face]: ...


ReaderFactory = Callable[[], Reader]
ProviderFactory = Callable[[Settings], LLMProvider]


def _video(project_dir: Path) -> Path:
    """The original film when it is still there, else the proxy."""
    try:
        source = config.source_link(project_dir).resolve()
    except (config.ProjectError, OSError):
        source = None
    return source if source is not None and source.is_file() else project_dir / PROXY


def _names_prompt_hash() -> str:
    text = resources.files("recap.prompts").joinpath("find_names.md").read_text(encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class CharactersStage:
    name = "characters"

    def __init__(self, reader_factory: ReaderFactory = FaceReader, provider_factory: ProviderFactory = get_provider) -> None:
        self._reader_factory = reader_factory
        self._provider_factory = provider_factory

    def outputs(self, project: Project) -> list[str]:
        return [CHARACTERS]

    def fingerprint(self, project_dir: Path, project: Project) -> str:
        sigs = [config.file_signature(p) for p in (_video(project_dir), project_dir / SCENES, project_dir / TRANSCRIPT)]
        return "|".join([*sigs, FACES_VERSION, project.settings.claude_model, _names_prompt_hash()])

    def run(self, ctx: StageContext) -> Project:
        video = _video(ctx.project_dir)
        if not video.is_file():
            raise StageFailure("input_missing", "Thiếu cả phim gốc lẫn proxy_480p.mp4, hãy chạy lại stage ingest")
        if video.name == Path(PROXY).name:
            ctx.emitter.log("warning", "Không còn phim gốc: tìm mặt trên bản 480p, mặt nhỏ có thể bị sót")
        key = "|".join([config.file_signature(video), FACES_VERSION])
        try:
            faces = self._faces(ctx, video, key)
            groups = cluster_faces(faces)
            beats = SCENES_ADAPTER.validate_json(ctx.path(SCENES).read_bytes()) if ctx.path(SCENES).is_file() else []
            characters = build_characters(faces, groups, beats, interval=1 / SAMPLE_FPS)
            pictures = self._pictures(ctx, video, faces, characters)
        finally:
            shutil.rmtree(ctx.path(FRAMES_DIR), ignore_errors=True)
        names, warning = self._names(ctx)
        self._keep_or_reset_choices(ctx, key)
        report = {
            "faces_key": key,
            "characters": [{"id": c.id, "faces": len(c.faces), "screen_sec": c.screen_sec, "beats": list(c.beats),
                            "pictures": pictures[c.id]} for c in characters],
            "names": [asdict(n) for n in names],
            "names_warning": warning,
        }  # fmt: skip
        config.write_json_atomic(ctx.path(CHARACTERS), report)
        if warning:
            ctx.emitter.log("warning", warning)
        ctx.emitter.log("info", f"{len(faces)} khuôn mặt, {len(characters)} nhân vật, {len(names)} tên trong lời thoại")
        ctx.emitter.artifact(self.name, CHARACTERS)
        return ctx.project

    # ---- faces -------------------------------------------------------------------------------

    def _frames(self, ctx: StageContext, video: Path) -> list[Path]:
        frames_dir = ctx.path(FRAMES_DIR)
        existing = sorted(frames_dir.glob("*.jpg"))
        if existing:
            return existing
        frames_dir.mkdir(parents=True, exist_ok=True)
        duration = ctx.project.media.duration if ctx.project.media else 0.0
        try:
            media.run_ffmpeg(["-i", str(video), "-vf", f"fps={SAMPLE_FPS:g},scale='min({FRAME_WIDTH},iw)':-2", "-q:v", "3",
                              str(frames_dir / "%05d.jpg")], total_sec=duration, on_progress=lambda _: None, log=ctx.log)  # fmt: skip
        except media.MediaError as exc:
            raise StageFailure("characters_failed", f"Không lấy được khung hình: {exc}", retryable=True) from exc
        return sorted(frames_dir.glob("*.jpg"))

    def _faces(self, ctx: StageContext, video: Path, key: str) -> list[Face]:
        cached = None if ctx.force else _load_faces(ctx.path(FACES_CACHE), key)
        if cached is not None:
            ctx.emitter.log("info", f"Dùng lại {len(cached)} khuôn mặt đã tìm")
            return cached
        frames = self._frames(ctx, video)
        try:
            reader = self._reader_factory()
            faces: list[Face] = []
            for n, frame in enumerate(frames):
                faces.extend(reader.read(frame, round(n / SAMPLE_FPS, 3)))
                if (n + 1) % PROGRESS_EVERY == 0 or n + 1 == len(frames):
                    ctx.emitter.progress(self.name, n + 1, len(frames), "Tìm khuôn mặt")
        except FaceError as exc:
            raise StageFailure("characters_failed", str(exc), retryable=True) from exc
        _save_faces(ctx.path(FACES_CACHE), key, faces)
        return faces

    def _pictures(self, ctx: StageContext, video: Path, faces: list[Face], characters: list[Character]) -> dict[str, list[str]]:
        """Face pictures per character (relative to work/), cropped from the frames when missing."""
        wanted = {c.id: [f"characters/faces/f{i:05d}.jpg" for i in c.samples] for c in characters}
        missing = [i for c in characters for i in c.samples if not ctx.path(f"{PICTURES_DIR}/f{i:05d}.jpg").is_file()]
        if missing:
            frames = self._frames(ctx, video)
            ctx.path(PICTURES_DIR).mkdir(parents=True, exist_ok=True)
            for index in missing:
                _crop(frames, faces[index], ctx.path(f"{PICTURES_DIR}/f{index:05d}.jpg"))
        return wanted

    # ---- names -------------------------------------------------------------------------------

    def _names(self, ctx: StageContext) -> tuple[list[NameCandidate], str | None]:
        path = ctx.path(TRANSCRIPT)
        if not path.is_file():
            return [], "Chưa có lời thoại (transcript.json) để tìm tên"
        settings = ctx.project.settings
        key = "|".join([config.file_signature(path), settings.claude_model, _names_prompt_hash()])
        cached = _load_names(ctx.path(NAMES_CACHE), key)
        if cached is not None:
            return cached, None
        dialogue = TRANSCRIPT_ADAPTER.validate_json(path.read_bytes())
        ctx.emitter.log("info", "Tìm tên nhân vật trong lời thoại: gọi Claude")
        try:
            provider = self._provider_factory(settings)
        except LLMError as exc:
            return [], f"Không lấy được tên từ lời thoại ({exc.message}); vẫn có thể tự gõ tên"
        names, warning = find_names(dialogue, provider)
        if warning is None:
            config.write_json_atomic(ctx.path(NAMES_CACHE), {"key": key, "names": [asdict(n) for n in names]})
        return names, warning

    def _keep_or_reset_choices(self, ctx: StageContext, key: str) -> None:
        """Choices name character ids; when the faces were found anew the ids mean other people."""
        try:
            previous = json.loads(ctx.path(CHARACTERS).read_text(encoding="utf-8")).get("faces_key")
        except (OSError, ValueError, AttributeError):
            previous = None
        if previous is not None and previous != key and ctx.path(CHOICES_FILE).is_file():
            save_choices(ctx.project_dir, Choices())
            ctx.emitter.log("warning", "Khuôn mặt đã được tìm lại từ đầu: hãy đặt tên nhân vật lại")


def _crop(frames: list[Path], face: Face, out: Path) -> None:
    index = min(len(frames) - 1, round(face.time * SAMPLE_FPS))
    with Image.open(frames[index]) as frame:
        width, height = frame.size
        x, y, w, h = face.box
        side = max(w * width, h * height) * (1 + 2 * PICTURE_MARGIN)
        cx, cy = (x + w / 2) * width, (y + h / 2) * height
        box = (int(max(0, cx - side / 2)), int(max(0, cy - side / 2)), int(min(width, cx + side / 2)), int(min(height, cy + side / 2)))
        frame.convert("RGB").crop(box).resize((PICTURE_SIZE, PICTURE_SIZE)).save(out, quality=88)


def _save_faces(path: Path, key: str, faces: list[Face]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez(tmp, key=np.array(key), times=np.array([f.time for f in faces], dtype=np.float64),
             boxes=np.array([f.box for f in faces], dtype=np.float32).reshape(-1, 4),
             scores=np.array([f.score for f in faces], dtype=np.float32),
             feats=np.array([f.feat for f in faces], dtype=np.float32).reshape(len(faces), -1))  # fmt: skip
    tmp.replace(path)


def _load_faces(path: Path, key: str) -> list[Face] | None:
    try:
        with np.load(path) as data:
            if str(data["key"]) != key:
                return None
            return [Face(time=float(t), box=tuple(map(float, b)), score=float(s), feat=f)  # type: ignore[arg-type]
                    for t, b, s, f in zip(data["times"], data["boxes"], data["scores"], data["feats"])]  # fmt: skip
    except (OSError, ValueError, KeyError):
        return None


def _load_names(path: Path, key: str) -> list[NameCandidate] | None:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") != key:
            return None
        return [NameCandidate(n["name"], int(n["count"]), tuple(n["examples"])) for n in data["names"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
