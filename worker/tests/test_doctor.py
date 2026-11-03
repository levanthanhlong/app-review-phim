from pathlib import Path

import pytest

from recap import doctor


def _by_name(checks: list[doctor.Check]) -> dict[str, doctor.Check]:
    return {c.name: c for c in checks}


def test_missing_tool_is_reported_with_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None if name == "ffmpeg" else f"/bin/{name}")

    checks = _by_name(doctor.run_checks(tmp_path, probe_ollama_server=False))

    assert checks["ffmpeg"].ok is False
    assert "brew install ffmpeg" in checks["ffmpeg"].hint
    assert checks["ffprobe"].ok is True


def test_storage_fails_when_volume_not_mounted(tmp_path: Path) -> None:
    check = doctor.check_storage(Path("/Volumes/__not_mounted__/RecapStudio"))
    assert check.ok is False
    assert "storage_unavailable" in check.detail


def test_storage_fails_when_low_space(tmp_path: Path) -> None:
    check = doctor.check_storage(tmp_path, min_free_gb=10**9)
    assert check.ok is False


def test_storage_ok_with_enough_space(tmp_path: Path) -> None:
    check = doctor.check_storage(tmp_path, min_free_gb=0)
    assert check.ok is True


def test_optional_checks_do_not_fail_overall() -> None:
    checks = [doctor.Check("a", True, ""), doctor.Check("ollama_server", False, "", required=False)]
    assert doctor.overall_ok(checks) is True
    assert doctor.overall_ok([*checks, doctor.Check("b", False, "")]) is False
