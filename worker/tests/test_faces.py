"""Face models (download, checksum) and which detected faces are good enough to recognise."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from recap.faces import MODELS, FaceError, FaceReader, ensure_models, is_usable


def row(x: float = 100, y: float = 80, size: float = 90, nose_shift: float = 0.0, score: float = 0.95) -> np.ndarray:
    """A YuNet detection: box, right eye, left eye, nose, mouth corners, score (pixels)."""
    rx, lx, eye_y = x + size * 0.3, x + size * 0.7, y + size * 0.4
    nose_x = (rx + lx) / 2 + nose_shift * (lx - rx)
    return np.array([x, y, size, size, rx, eye_y, lx, eye_y, nose_x, y + size * 0.6,
                     rx, y + size * 0.8, lx, y + size * 0.8, score], dtype=np.float32)  # fmt: skip


def test_a_big_clear_face_looking_at_the_camera_is_usable() -> None:
    assert is_usable(row(), frame_width=960)


@pytest.mark.parametrize(
    ("detection", "why"),
    [
        (row(size=30), "too small to recognise"),
        (row(score=0.7), "detector unsure"),
        (row(nose_shift=0.5), "turned away: the nose is past an eye"),
    ],
)
def test_faces_that_would_confuse_the_grouping_are_left_out(detection: np.ndarray, why: str) -> None:
    assert not is_usable(detection, frame_width=960), why


def fake_models(tmp_path: Path) -> dict[str, tuple[str, str]]:
    data = {"det.onnx": b"detector", "rec.onnx": b"recogniser"}
    return {name: (f"https://example.com/{name}", hashlib.sha256(body).hexdigest()) for name, body in data.items()}


def test_models_are_downloaded_once_and_checked(tmp_path: Path) -> None:
    models = fake_models(tmp_path)
    fetched: list[str] = []

    def fetch(url: str, dest: Path) -> None:
        fetched.append(url)
        dest.write_bytes(b"detector" if url.endswith("det.onnx") else b"recogniser")

    paths = ensure_models(tmp_path / "models", models=models, fetch=fetch)
    ensure_models(tmp_path / "models", models=models, fetch=fetch)  # second time: nothing to fetch

    assert len(fetched) == 2 and all(p.is_file() for p in paths.values())


def test_a_corrupt_download_is_removed_and_reported(tmp_path: Path) -> None:
    models = fake_models(tmp_path)

    with pytest.raises(FaceError):
        ensure_models(tmp_path / "models", models=models, fetch=lambda url, dest: dest.write_bytes(b"html error page"))

    assert not any((tmp_path / "models").glob("*.onnx"))


def test_known_models_have_pinned_checksums() -> None:
    assert set(MODELS) == {"face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx"}
    assert all(len(sha) == 64 for _, sha in MODELS.values())


MODELS_DIR = Path("/Volumes/ExternalSSD/cache/recap-models")


@pytest.mark.skipif(not (MODELS_DIR / "face_recognition_sface_2021dec.onnx").is_file(), reason="face models not downloaded")
def test_a_picture_without_people_has_no_faces(tmp_path: Path) -> None:
    picture = tmp_path / "sky.jpg"
    Image.new("RGB", (960, 540), (90, 140, 200)).save(picture)

    assert FaceReader(MODELS_DIR).read(picture, time=3.0) == []
