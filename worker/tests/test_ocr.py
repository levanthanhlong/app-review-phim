"""macOS Vision OCR on a drawn image."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from recap.ocr import recognize

FONT = Path("/System/Library/Fonts/Supplemental/Arial.ttf")


@pytest.mark.skipif(sys.platform != "darwin" or not FONT.is_file(), reason="macOS Vision only")
def test_reads_vietnamese_and_domains_with_top_left_boxes(tmp_path: Path) -> None:
    # Arrange: a 854x480 frame with a domain bottom-left and a credit line near the top
    image = Image.new("RGB", (854, 480), (30, 30, 30))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 28)
    draw.text((60, 420), "youtube.com/FAPtivi", fill="white", font=font)
    draw.text((200, 60), "Đạo diễn: Trần Văn A", fill="white", font=font)
    path = tmp_path / "frame.jpg"
    image.save(path)

    # Act
    boxes = recognize(path)

    # Assert
    by_text = {b.text: b for b in boxes}
    assert "youtube.com/FAPtivi" in by_text and by_text["youtube.com/FAPtivi"].conf >= 0.5
    domain = by_text["youtube.com/FAPtivi"]
    assert domain.y > 0.8 and domain.x < 0.15  # origin top-left: bottom of the frame is y≈0.9
    assert any("Trần Văn A" in b.text and b.y < 0.25 for b in boxes)
