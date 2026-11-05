"""Faces in a frame: OpenCV Zoo's YuNet (detection, MIT) and SFace (recognition, Apache-2.0).

The models (~39 MB) are downloaded on first use into the models folder on the external SSD and
checked against pinned SHA-256 sums. Only faces big, sure and roughly facing the camera are kept:
profiles and tiny faces give features that mix people up.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

import numpy as np

from recap.characters import Face
from recap.config import EXTERNAL_VOLUME

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
DETECTOR = "face_detection_yunet_2023mar.onnx"
RECOGNISER = "face_recognition_sface_2021dec.onnx"
MODELS: dict[str, tuple[str, str]] = {
    DETECTOR: (f"{ZOO}/face_detection_yunet/{DETECTOR}", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    RECOGNISER: (f"{ZOO}/face_recognition_sface/{RECOGNISER}", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}  # fmt: skip

MIN_SCORE = 0.9  # detector confidence
MIN_FACE_SHARE = 0.05  # face width as a share of the frame width (about 50 px at 960)
MAX_NOSE_OFFSET = 0.35  # nose off the eyes' midpoint, in eye distances: more is a profile
DETECT_SCORE = 0.8  # YuNet's own threshold (the stricter MIN_SCORE is applied after)

Fetch = Callable[[str, Path], None]


class FaceError(Exception):
    pass


def models_dir() -> Path:
    """RECAP_MODELS_DIR, else the external SSD's cache, else ~/.cache."""
    if override := os.environ.get("RECAP_MODELS_DIR"):
        return Path(override)
    if EXTERNAL_VOLUME.is_dir():
        return EXTERNAL_VOLUME / "cache" / "recap-models"
    return Path.home() / ".cache" / "recap-models"


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, dest.open("wb") as out:  # noqa: S310 (fixed https URL)
        while chunk := response.read(1 << 20):
            out.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_models(directory: Path, models: Mapping[str, tuple[str, str]] = MODELS, fetch: Fetch = _download) -> dict[str, Path]:
    """Paths of the models, downloading (and checking) the missing ones."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, (url, sha) in models.items():
        path = directory / name
        if not path.is_file() or _sha256(path) != sha:
            partial = path.with_suffix(".part")
            try:
                fetch(url, partial)
            except OSError as exc:
                partial.unlink(missing_ok=True)
                raise FaceError(f"Không tải được model nhận diện mặt {name}: {exc}") from exc
            if _sha256(partial) != sha:
                partial.unlink(missing_ok=True)
                raise FaceError(f"Model {name} tải về bị hỏng (sai mã SHA-256), hãy thử lại")
            partial.replace(path)
        paths[name] = path
    return paths


def is_usable(detection: np.ndarray, frame_width: int) -> bool:
    """A YuNet row [x, y, w, h, right eye, left eye, nose, mouth corners, score] worth recognising."""
    width, score = float(detection[2]), float(detection[14])
    if score < MIN_SCORE or width < MIN_FACE_SHARE * frame_width:
        return False
    rx, ry, lx, ly, nx = (float(v) for v in detection[4:9])
    eyes = float(np.hypot(lx - rx, ly - ry))
    return eyes > 0 and abs(nx - (rx + lx) / 2) / eyes <= MAX_NOSE_OFFSET


class FaceReader:
    """Detects and recognises the usable faces of one image at a time."""

    def __init__(self, directory: Path | None = None) -> None:
        import cv2

        paths = ensure_models(directory or models_dir())
        self._detector = cv2.FaceDetectorYN.create(str(paths[DETECTOR]), "", (320, 320), DETECT_SCORE)
        self._recogniser = cv2.FaceRecognizerSF.create(str(paths[RECOGNISER]), "")

    def read(self, image: Path, time: float) -> list[Face]:
        import cv2

        picture = cv2.imread(str(image))
        if picture is None:
            raise FaceError(f"Không đọc được ảnh {image.name}")
        height, width = picture.shape[:2]
        self._detector.setInputSize((width, height))
        _, found = self._detector.detect(picture)
        faces = []
        for detection in found if found is not None else []:
            if not is_usable(detection, width):
                continue
            feat = self._recogniser.feature(self._recogniser.alignCrop(picture, detection)).flatten()
            x, y, w, h = (float(v) for v in detection[:4])
            faces.append(Face(time=time, box=(x / width, y / height, w / width, h / height), score=float(detection[14]),
                              feat=feat / np.linalg.norm(feat)))  # fmt: skip
        return faces
