"""Reject incorrect parity claims and preserve the exact SQL statement boundary."""
import copy
import hashlib
import json
from decimal import Decimal

import pytest

from mart.databricks_validation import compare_rows, scalar, split_sql, write_bundle


@pytest.fixture
def reference():
    return {"types": ["VARCHAR", "DECIMAL(18,4)", "DOUBLE", "BIGINT"],
            "rows": [["A", "10.0000", "0.5", 1], ["B", "-3.0000", None, 0]]}


def test_parity_ignores_order_and_decimal_scale_but_preserves_values(reference):
    assert compare_rows(reference, [["B", Decimal("-3"), None, 0], ["A", Decimal("10"), 0.50000000001, 1]])


@pytest.mark.parametrize("bad_rows", [
    [["A", "10.0001", "0.5", 1], ["B", "-3", None, 0]],  # money is exact
    [["A", "10", "0.5", 1]],  # missing observed zero-activity period
    [["A", "10", "0.5", 1], ["B", "-3", "0", 0]],  # NULL ratio is not zero
    [["A", "10", "0.5", 1], ["A", "10", "0.5", 1]],  # duplicate replaces other row
    [["A", "10", "0.6", 1], ["B", "-3", None, 0]],  # changed ratio
])
def test_parity_rejects_financial_and_cohort_counterexamples(reference, bad_rows):
    assert not compare_rows(reference, bad_rows)


def test_parity_preserves_duplicate_multiplicity(reference):
    duplicate = copy.deepcopy(reference)
    duplicate["rows"].append(duplicate["rows"][0])
    assert not compare_rows(duplicate, reference["rows"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("NaN")])
def test_non_finite_output_cannot_enter_evidence(value):
    with pytest.raises(ValueError, match="Non-finite"):
        scalar(value)


def test_statement_split_preserves_quotes_and_ignores_comments():
    statements = split_sql("-- misleading ;\nselect 'x;y', 'it''s', `a;b`; /* ; */ select '\\\\s+';")
    assert statements == ["select 'x;y', 'it''s', `a;b`", "select '\\\\s+'"]
    with pytest.raises(ValueError, match="Unterminated"):
        split_sql("select 'broken")


def test_bundle_has_executed_reference_but_never_claims_cloud_execution(tmp_path):
    raw = tmp_path / "input.csv"
    raw.write_text("Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country\n"
                   "1,10001,Mug,1,2011-01-01 12:00:00,3,1001,United Kingdom\n", encoding="utf-8")
    destination = tmp_path / "bundle"
    payload = write_bundle(raw, destination)
    assert payload["hosted_status"] == "NOT_RUN"
    assert payload["source_sha256"] == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert payload["scenarios"]["full"]["counts"]["fact_lines"] == 1
    assert len(payload["scenarios"]["returns_only_and_cohort"]["rows"]["mart_cohort"]["rows"]) == 3
    notebook = json.loads((destination / "Finance_Mart_Revalidation.ipynb").read_text())
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), "generated_notebook", "exec")
            assert cell["outputs"] == []
    with pytest.raises(FileExistsError):
        write_bundle(raw, destination)
