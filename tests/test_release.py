from decimal import Decimal
from unittest.mock import Mock

import pytest

from mart import release
from mart.excel import build_workbook


@pytest.mark.parametrize("value", [None, "12.5", True, float("nan"), float("inf"), 12.51])
def test_workbook_comparison_rejects_missing_non_numeric_or_wrong_cache(value):
    with pytest.raises(ValueError, match="differs from SQL"):
        release.verify_value(value, Decimal("12.50"), "Summary!B4")


def test_numeric_tolerance_and_undefined_ratio_are_explicit():
    release.verify_value(12.5 + 1e-12, Decimal("12.50"), "money")
    release.verify_value(None, None, "undefined")
    release.verify_value("", None, "undefined")
    with pytest.raises(ValueError, match="undefined ratio"):
        release.verify_value(0, None, "undefined")


def test_uncalculated_workbook_is_not_a_valid_release(con, tmp_path):
    path = tmp_path / "workbook.xlsx"
    build_workbook(con, path)
    with pytest.raises(ValueError, match="cached workbook value None"):
        release.validate_workbook(con, path)


def test_manifest_checksums_change_only_for_changed_artifact(tmp_path):
    for relative in release.ARTIFACTS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    first = release.artifact_records(tmp_path)
    assert first == release.artifact_records(tmp_path)
    (tmp_path / "explorer/index.html").write_bytes(b"changed")
    second = release.artifact_records(tmp_path)
    changed = [before["path"] for before, after in zip(first, second, strict=True) if before != after]
    assert changed == ["explorer/index.html"]
    (tmp_path / "img/revenue.png").write_bytes(b"")
    with pytest.raises(ValueError, match="Missing or empty"):
        release.artifact_records(tmp_path)


def test_failed_calculation_never_publishes_partial_bundle(tmp_path, monkeypatch):
    con = Mock()
    monkeypatch.setattr(release, "build", lambda *args: con)
    monkeypatch.setattr(release, "run_checks", lambda con: [])
    monkeypatch.setattr(release, "write_report", lambda *args: None)
    monkeypatch.setattr(release, "build_workbook", lambda *args: None)
    monkeypatch.setattr(release, "build_explorer", lambda *args: None)
    monkeypatch.setattr(release, "draw_all", lambda *args: None)
    monkeypatch.setattr(release, "recalculate", lambda *args: False)
    monkeypatch.setenv("GITHUB_SHA", "test-revision")
    output = tmp_path / "release"
    (tmp_path / "raw.csv").write_text("frozen fixture")
    with pytest.raises(RuntimeError, match="LibreOffice is required"):
        release.release(tmp_path / "raw.csv", output)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == [tmp_path / "raw.csv"]
    con.close.assert_called_once()


def test_release_never_overwrites_existing_bundle(tmp_path):
    (tmp_path / "manifest.json").write_text("previous release")
    with pytest.raises(FileExistsError, match="must be new"):
        release.release(tmp_path / "raw.csv", tmp_path)
    assert (tmp_path / "manifest.json").read_text() == "previous release"
