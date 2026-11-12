"""Counterexamples for availability, outcome maturity, identity and publication."""

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
import sklearn

from mart.customer_value import (
    FEATURES,
    RecencyBaseline,
    assess,
    classification_metrics,
    paired_interval,
    ranked_selection,
    train_select,
    validate_splits,
)
from mart.customer_value import (
    snapshot as customer_snapshot,
)
from mart.value_cli import digest, publish
from mart.value_score import score


def snapshot(con, cutoff, coverage_end, **kwargs):
    # The small fixture declares complete coverage including days with no orders.
    kwargs.setdefault("coverage_start", "2009-10-01")
    return customer_snapshot(con, cutoff, coverage_end, **kwargs)


@pytest.fixture
def events():
    con = duckdb.connect()
    con.execute("""create table fact_lines_base (
        customer_id varchar, invoice_no varchar, stock_code varchar,
        invoiced_at timestamp, amount double, line_type varchar)""")
    con.executemany(
        "insert into fact_lines_base values (?, ?, ?, ?, ?, ?)",
        [
            ("A", "old", "x", "2010-01-01 10:00", 100, "sale"),
            ("A", "one", "x", "2010-03-01 10:00", 20, "sale"),
            ("A", "one", "y", "2010-03-01 10:00", 30, "sale"),
            ("A", "return", "x", "2010-03-02 10:00", -10, "return"),
            ("A", "fee", "POST", "2010-03-02 10:00", 200, "shipping"),
            ("A", "boundary", "x", "2010-04-01 00:00", 70, "sale"),
            ("A", "future_return", "x", "2010-04-02 00:00", -70, "return"),
            ("A", "excluded", "x", "2010-06-30 00:00", 999, "sale"),
            ("B", "two", "x", "2010-03-31 23:59", 40, "sale"),
            ("NEW", "new", "x", "2010-04-01 00:00", 50, "sale"),
            ("RETURN_ONLY", "r", "x", "2010-03-02 10:00", -10, "return"),
            (None, "guest", "x", "2010-03-02 10:00", 10, "sale"),
        ],
    )
    yield con
    con.close()


def test_half_open_features_labels_and_registered_eligibility(events):
    result = snapshot(events, "2010-04-01", "2010-07-01").set_index("customer_id")
    assert list(result.index) == ["A", "B"]
    assert result.loc["A", "orders_180d"] == 2  # invoice grain, not line count
    assert result.loc["A", "products_180d"] == 2
    assert result.loc["A", "gross_90d"] == 150  # Jan 1 is exactly the included 90-day start
    assert result.loc["A", "gross_180d"] == 150
    assert result.loc["A", "returns_180d"] == 10
    assert result.loc["A", "future_gross_90d"] == 70  # exact cutoff included, end excluded
    assert result.loc["B", "future_gross_90d"] == 0
    assert result.loc["A", "repeat_90d"] == 1
    assert result.loc["B", "repeat_90d"] == 0


def test_appending_future_data_cannot_change_features_or_eligibility(events):
    before = snapshot(events, "2010-04-01", "2010-07-01")
    events.execute("insert into fact_lines_base values ('A', 'later', 'z', '2010-04-04', 999999, 'sale')")
    after = snapshot(events, "2010-04-01", "2010-07-01")
    pd.testing.assert_frame_equal(before[["customer_id", *FEATURES]], after[["customer_id", *FEATURES]])
    assert after["future_gross_90d"].sum() - before["future_gross_90d"].sum() == 999999


def test_training_and_shadow_scoring_share_identical_feature_definitions(events):
    labelled = snapshot(events, "2010-04-01", "2010-07-01")
    serving = snapshot(events, "2010-04-01", "2010-04-01", labelled=False)
    pd.testing.assert_frame_equal(labelled[["cutoff", "customer_id", *FEATURES]], serving)


def test_full_history_customer_dimensions_are_never_used(events):
    before = snapshot(events, "2010-04-01", "2010-07-01")
    events.execute("create table dim_customer as select 'A' customer_id, 999999 future_revenue")
    pd.testing.assert_frame_equal(before, snapshot(events, "2010-04-01", "2010-07-01"))


def test_unmatured_windows_are_not_silently_negative_labels(events):
    with pytest.raises(ValueError, match="Unmatured"):
        snapshot(events, "2010-04-01", "2010-06-29")
    shadow = snapshot(events, "2010-04-01", "2010-04-01", labelled=False)
    assert "repeat_90d" not in shadow and "future_gross_90d" not in shadow


def test_incomplete_observation_history_is_rejected(events):
    with pytest.raises(ValueError, match="180-day observation"):
        snapshot(events, "2010-04-01", "2010-07-01", coverage_start="2010-01-01")


def test_fixed_horizon_cannot_silently_change_target_meaning(events):
    with pytest.raises(ValueError, match="90-day horizon"):
        snapshot(events, "2010-04-01", "2010-07-01", horizon=30)


@pytest.mark.parametrize("cutoff", ["NaT", "2010-04-01T10:00", "2010-04-01T00:00Z"])
def test_ambiguous_cutoff_rejected(events, cutoff):
    with pytest.raises(ValueError):
        snapshot(events, cutoff, "2010-07-01")


def test_splits_require_mature_labels_before_next_prediction_date():
    validate_splits(["2010-01-01"], "2010-04-01", ["2010-07-01"], "2010-10-01")
    with pytest.raises(ValueError, match="disjoint"):
        validate_splits(["2010-01-01"], "2010-03-31", ["2010-07-01"], "2010-10-01")
    with pytest.raises(ValueError, match="fully observed"):
        validate_splits(["2010-01-01"], "2010-04-01", ["2010-07-01"], "2010-09-01")


def test_capacity_policy_is_exact_and_tie_breaking_is_permutation_stable():
    frame = pd.DataFrame({"customer_id": ["B", "A", "D", "E", "C"]})
    selected = ranked_selection(frame, np.ones(5))
    assert frame.loc[selected, "customer_id"].tolist() == ["A"]
    reordered = frame.iloc[::-1].reset_index(drop=True)
    assert reordered.loc[ranked_selection(reordered, np.ones(5)), "customer_id"].tolist() == ["A"]


def test_probability_metrics_and_baseline_have_independent_small_oracles():
    result = classification_metrics([0, 1], [0.25, 0.75])
    assert result["brier"] == pytest.approx(0.0625)
    assert result["roc_auc"] == 1
    assert classification_metrics([0, 0], [0.25, 0.75])["roc_auc"] is None
    frame = pd.DataFrame({"recency_days": [1, 2, 40, 50]})
    baseline = RecencyBaseline().fit(frame, np.array([1, 1, 0, 0]))
    assert baseline.predict_proba(frame)[0, 1] == pytest.approx(7 / 12)


def test_assessment_rejects_identity_mismatch():
    frame = pd.DataFrame({"cutoff": ["2010-01-01"], "customer_id": ["A"]})
    scores = pd.DataFrame({"cutoff": ["2010-01-01"], "customer_id": ["B"]})
    with pytest.raises(ValueError, match="identities"):
        assess(frame, scores)


def test_paired_bootstrap_preserves_constant_difference_and_order():
    assert paired_interval(np.ones(5)) == [1, 1]
    assert paired_interval(np.zeros(5)) == [0, 0]


def test_training_rejects_degenerate_target():
    frame = pd.DataFrame({name: [1, 2] for name in FEATURES})
    frame["repeat_90d"] = 1
    with pytest.raises(ValueError, match="both"):
        train_select(frame, frame)


def test_failure_does_not_publish_partial_bundle_or_modify_previous_version(tmp_path):
    prior = tmp_path / "previous"
    prior.mkdir()
    (prior / "report.json").write_bytes(b"last good")

    def fail(stage: Path):
        (stage / "report.json").write_bytes(b"partial")
        raise RuntimeError("generation failed")

    with pytest.raises(RuntimeError, match="generation failed"):
        publish(tmp_path / "candidate", fail)
    assert not (tmp_path / "candidate").exists()
    assert (prior / "report.json").read_bytes() == b"last good"
    assert not list(tmp_path.glob(".customer-value-*"))
    with pytest.raises(FileExistsError):
        publish(prior, fail)


def test_success_publishes_complete_new_version(tmp_path):
    def generate(stage):
        (stage / "report.json").write_text("{}")
        (stage / "scores.csv").write_text("complete")
        return {"passed": True}

    assert publish(tmp_path / "new", generate) == {"passed": True}
    assert {p.name for p in (tmp_path / "new").iterdir()} == {"report.json", "scores.csv"}


def test_batch_scoring_checks_identity_before_deserializing(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "model.joblib").write_bytes(b"not a serialized model")
    (bundle / "report.json").write_text(json.dumps({"splits": {"validation": "2011-01-01"}, "contract": {"horizon_days": 90}}))
    manifest = {
        "artifacts": {name: {"sha256": digest(bundle / name)} for name in ["model.joblib", "report.json"]},
        "runtime": {"scikit_learn": sklearn.__version__},
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="not available"):
        score(bundle, tmp_path / "unused.csv", "2011-03-31", "2011-12-09", tmp_path / "scores")
    (bundle / "model.joblib").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        score(bundle, tmp_path / "unused.csv", "2011-12-09", "2011-12-09", tmp_path / "scores")
