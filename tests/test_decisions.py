import json
from pathlib import Path

import duckdb
import pytest

from mart import decisions, release
from mart.provenance import code_records, freeze_source, sha256


@pytest.fixture
def decisions_con():
    con = duckdb.connect()
    con.execute("""create table fact_lines(invoice_date date, line_type varchar, amount decimal(16,2),
        invoice_no varchar, customer_id integer, is_quick_reversal boolean, country varchar)""")
    con.execute("""insert into fact_lines values
        ('2011-01-10','sale',100,'A',1,false,'United Kingdom'),
        ('2011-01-12','return',-20,'CA',1,false,'United Kingdom'),
        ('2011-02-10','sale',60,'B',NULL,false,'United Kingdom'),
        ('2011-02-10','sale',90,'C',2,false,'Germany'),
        ('2011-02-11','return',-30,'CB',1,true,'United Kingdom'),
        ('2011-03-03','sale',9999,'D',2,false,'Germany')""")
    yield con
    con.close()


def test_complete_month_comparison_and_exact_arithmetic_drivers(decisions_con):
    report = decisions.decision_report(decisions_con, "2011-01-01", "2011-03-09")
    assert report["current"]["month"] == "2011-02-01"
    assert report["current"]["net_product_revenue"] == 120
    assert report["current"]["guest_sales_share"] == .4
    assert report["previous"]["net_product_revenue"] == 80
    assert report["drivers"] == {
        "gross_sales_change": 50, "returns_change": -10, "net_revenue_change": 40,
        "order_volume_effect": 87.5, "basket_value_effect": -37.5,
    }
    assert [(row["country"], row["delta"]) for row in report["country_contributions"]] == [
        ("Germany", 90), ("United Kingdom", -50),
    ]
    assert report["previous_year"] is None
    assert report["month_over_month"]["changes"]["net_product_revenue"]["relative_change"] == .5
    assert "2011-02" in decisions.render_markdown(report)
    assert "causal" in decisions.render_markdown(report)


def test_returns_only_month_preserves_net_change_without_fake_order_driver(decisions_con):
    decisions_con.execute("delete from fact_lines where invoice_date >= '2011-03-01'")
    decisions_con.execute("insert into fact_lines values ('2011-03-02','return',-5,'CE',1,false,'Germany')")
    report = decisions.decision_report(decisions_con, "2011-01-01", "2011-04-01")
    assert report["current"]["net_product_revenue"] == -5
    assert report["current"]["avg_order_value"] is None
    assert report["current"]["return_rate"] is None
    assert report["drivers"]["order_volume_effect"] is None
    assert report["drivers"]["basket_value_effect"] is None
    assert sum(row["delta"] for row in report["country_contributions"]) == -125
    json.dumps(report, allow_nan=False)


def test_partial_first_month_is_not_used_as_comparison(decisions_con):
    report = decisions.decision_report(decisions_con, "2011-01-15", "2011-03-01")
    assert report["current"]["month"] == "2011-02-01"
    assert report["previous"] is None and report["drivers"] is None
    assert report["month_over_month"] is None


@pytest.mark.parametrize("start,end", [("2011-01-15", "2011-02-01"), ("2011-03-01", "2011-01-01")])
def test_incomplete_coverage_is_rejected(decisions_con, start, end):
    with pytest.raises(ValueError, match="complete calendar month"):
        decisions.decision_report(decisions_con, start, end)


def test_future_and_partial_month_rows_do_not_change_decision(decisions_con):
    first = decisions.decision_report(decisions_con, "2011-01-01", "2011-03-09")
    decisions_con.execute("update fact_lines set amount=888888 where invoice_date >= '2011-03-01'")
    assert decisions.decision_report(decisions_con, "2011-01-01", "2011-03-09") == first


def test_frozen_copy_identifies_consumed_bytes_if_original_changes(tmp_path):
    raw = tmp_path / "input.csv"
    raw.write_bytes(b"original input\n")
    frozen, source = freeze_source(raw, tmp_path / "private-copy")
    raw.write_bytes(b"replaced source\n")
    assert frozen.read_bytes() == b"original input\n"
    assert source["sha256"] == sha256(frozen) != sha256(raw)
    assert source["file"] == "input.csv"
    assert str(tmp_path) not in json.dumps(source)


def test_release_manifest_tracks_copy_not_mutated_original(tmp_path, monkeypatch):
    raw = tmp_path / "input.csv"
    raw.write_bytes(b"reviewed input\n")
    expected = sha256(raw)
    con = duckdb.connect()
    for table in ("raw_lines", "fact_lines", "quarantine_lines"):
        con.execute(f"create table {table}(n integer)")

    def build(frozen, database):
        assert Path(frozen) != raw and Path(frozen).read_bytes() == b"reviewed input\n"
        raw.write_bytes(b"source replaced during workbook build\n")
        return con

    def artifact(_con, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"complete fixture artifact")

    def explorer(_con, path):
        artifact(_con, path / "index.html")

    def charts(_con, path):
        for name in ("revenue.png", "return_rate.png", "cohorts.png"):
            artifact(_con, path / name)

    monkeypatch.setenv("GITHUB_SHA", "fixture-revision")
    monkeypatch.setattr(release, "build", build)
    monkeypatch.setattr(release, "run_checks", lambda con: [])
    monkeypatch.setattr(release, "write_report", artifact)
    monkeypatch.setattr(release, "build_workbook", artifact)
    monkeypatch.setattr(release, "build_explorer", explorer)
    monkeypatch.setattr(release, "draw_all", charts)
    monkeypatch.setattr(release, "recalculate", lambda path: True)
    monkeypatch.setattr(release, "validate_workbook", lambda con, path: {"fixture": True})
    manifest = release.release(raw, tmp_path / "release")
    assert manifest["source"]["sha256"] == expected != sha256(raw)
    assert manifest["source"]["bytes"] == len(b"reviewed input\n")
    assert "mart/release.py" in manifest["code_sha256_lf_normalized"]
    assert len(manifest["artifacts"]) == len(release.ARTIFACTS)


def test_management_cli_failed_build_never_publishes(tmp_path, monkeypatch):
    raw = tmp_path / "input.csv"
    raw.write_text("invalid data")
    output = tmp_path / "report"
    monkeypatch.setattr("sys.argv", ["decisions", "--raw", str(raw), "--output", str(output),
                                    "--coverage-start", "2011-01-01", "--coverage-end", "2011-03-01"])
    def fail(raw):
        raise ValueError("source rejected")
    monkeypatch.setattr(decisions, "build", fail)
    with pytest.raises(ValueError, match="source rejected"):
        decisions.main()
    assert not output.exists()
    assert list(tmp_path.iterdir()) == [raw]


def test_code_identity_is_portable_across_git_newline_conversion(tmp_path):
    code = tmp_path / "mart/example.py"
    code.parent.mkdir()
    code.write_bytes(b"a = 1\r\nb = 2\r\n")
    first = code_records(tmp_path)
    code.write_bytes(b"a = 1\nb = 2\n")
    assert code_records(tmp_path) == first
    code.write_bytes(b"a = 3\nb = 2\n")
    assert code_records(tmp_path) != first
