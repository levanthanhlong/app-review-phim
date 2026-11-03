"""Real Ollama checks. Skipped when the server or the model is not available."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from recap.ollama_vl import DescribeError, OllamaDescriber, server_is_up
from recap.stages.describe import build_prompt, clean_description
from tests.conftest import HAS_FFMPEG

MODEL = "qwen2.5vl:7b"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not server_is_up(), reason="ollama server not running"),
]


def _has_model(name: str) -> bool:
    proc = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    return proc.returncode == 0 and name in proc.stdout


def test_missing_model_is_reported_with_pull_hint() -> None:
    with pytest.raises(DescribeError) as exc:
        OllamaDescriber("khong-ton-tai:1b")
    assert exc.value.code == "model_missing" and "ollama pull khong-ton-tai:1b" in exc.value.message


@pytest.mark.skipif(not (HAS_FFMPEG and _has_model(MODEL)), reason=f"{MODEL} not pulled")
def test_describes_an_image_as_one_vietnamese_sentence(tmp_path: Path) -> None:
    # Arrange: a test pattern frame (content checks are done on real keyframes in the acceptance run)
    image = tmp_path / "frame.jpg"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=512x288", "-frames:v", "1", str(image)],
        check=True,
    )
    describer = OllamaDescriber(MODEL)

    # Act
    try:
        text = clean_description(describer.describe(image, build_prompt("Chào mọi người")))
    finally:
        describer.close()

    # Assert
    assert text and len(text.split()) <= 40
    assert any(ch in text.lower() for ch in "ăâđêôơưàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũ")  # Vietnamese
    assert not text.lower().startswith(("nhân vật:", "trong ảnh", "cảnh này"))
