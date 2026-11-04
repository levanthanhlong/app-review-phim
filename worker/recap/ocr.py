"""Text in an image, with macOS Vision (on-device, free; reads Vietnamese). Used by the `inspect` stage."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from recap.inspect_rules import TextBox

LANGUAGES = ["vi-VT", "en-US"]
ACCURATE = 0  # VNRequestTextRecognitionLevelAccurate: the fast level misreads overlaid text

Ocr = Callable[[Path], list[TextBox]]


class OcrError(Exception):
    pass


def recognize(image: Path) -> list[TextBox]:
    """Every line of text Vision finds in `image`; boxes normalized, origin top-left."""
    try:
        import objc
        import Vision  # noqa: F401  (imported here so non-macOS machines can still import this module)
        from Foundation import NSURL  # noqa: F401
    except ImportError as exc:  # not macOS, or pyobjc missing
        raise OcrError("Thiếu macOS Vision (pyobjc-framework-Vision), hãy chạy lại setup.sh") from exc
    with objc.autorelease_pool():  # thousands of calls per film: free Vision's objects each time
        return _recognize(image)


def _recognize(image: Path) -> list[TextBox]:
    import Vision
    from Foundation import NSURL

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(ACCURATE)
    request.setRecognitionLanguages_(LANGUAGES)
    request.setUsesLanguageCorrection_(False)  # names, domains and slogans are not dictionary words
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(image)), None)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise OcrError(f"Vision không đọc được {image.name}: {error}")
    boxes = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        candidate = candidates[0]
        b = observation.boundingBox()  # normalized, origin bottom-left
        boxes.append(TextBox(text=str(candidate.string()), conf=round(float(candidate.confidence()), 3),
                             x=round(b.origin.x, 4), y=round(1 - b.origin.y - b.size.height, 4),
                             w=round(b.size.width, 4), h=round(b.size.height, 4)))  # fmt: skip
    return boxes
