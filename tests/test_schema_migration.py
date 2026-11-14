"""Publication fault boundaries and hand-computed contract compatibility."""

import copy

import pytest

from mart.dbt_warehouse import Warehouse, canonical
from mart.schema_migration import CONTRACTS, ReleasePublisher, expected_rows, fixture

CUTOFF = "2026-04-01T00:00:00Z"


def materialize(warehouse, rows):
    with warehouse.connection() as con:
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {warehouse.schema}")
        for name, columns in CONTRACTS.items():
            definitions = [
                key
                + (
                    " BIGINT"
                    if key.endswith("_cents")
                    else " BOOLEAN"
                    if key == "previously_paying"
                    else " DATE"
                    if key == "month"
                    else " VARCHAR"
                )
                for key in columns
            ]
            con.execute(f"CREATE OR REPLACE TABLE {warehouse.schema}.{name} ({','.join(definitions)})")
            for row in rows[name]:
                con.execute(
                    f"INSERT INTO {warehouse.schema}.{name} VALUES ({','.join('?' for _ in columns)})", [row[key] for key in columns]
                )


@pytest.fixture
def setup(tmp_path):
    warehouse = Warehouse("duckdb", tmp_path)
    publisher = ReleasePublisher(warehouse)
    source = fixture()
    materialize(warehouse, expected_rows(source, CUTOFF))
    publisher.stage("old", source, CUTOFF)
    publisher.promote("old")
    yield warehouse, publisher, source
    publisher.close()
    warehouse.close()


def test_hand_calculated_credit_does_not_reduce_other_invoice_exposure():
    rows = expected_rows(fixture(), CUTOFF)["invoice_balances_v2"]
    assert sum(row["gross_unpaid_cents"] for row in rows) == 4000
    assert sum(row["credit_cents"] for row in rows) == 1500
    assert sum(row["net_balance_cents"] for row in rows) == 2500
    assert rows[0]["net_balance_cents"] == -1500


@pytest.mark.parametrize("boundary", range(1, 6))
def test_each_interrupted_view_replacement_restores_all_consumers(setup, boundary):
    warehouse, publisher, source = setup
    old = canonical(publisher.consume())
    changed = copy.deepcopy(source)
    changed["payments"][1]["amount_cents"] = 2000
    materialize(warehouse, expected_rows(changed, CUTOFF))
    publisher.stage("new", changed, CUTOFF)
    with pytest.raises(RuntimeError, match="Injected"):
        publisher.promote("new", fail_after_view=boundary)
    assert canonical(publisher.consume()) == old
    assert publisher.view_rows() == expected_rows(source, CUTOFF)
    publisher.promote("new")
    assert publisher.consume()["rows"] == expected_rows(changed, CUTOFF)
    publisher.promote("old")
    assert canonical(publisher.consume()) == old
    assert publisher.view_rows() == expected_rows(source, CUTOFF)


def test_reader_pins_immutable_release_before_second_connection_promotes(setup):
    warehouse, publisher, source = setup
    before = publisher.consume()
    changed = copy.deepcopy(source)
    changed["payments"][1]["amount_cents"] = 2000
    materialize(warehouse, expected_rows(changed, CUTOFF))
    publisher.stage("new", changed, CUTOFF)
    pinned = publisher.consume(_after_pin=lambda: publisher.promote("new"))
    assert pinned == before
    assert publisher.consume()["release_id"] == "new"
    assert publisher.view_rows() == expected_rows(changed, CUTOFF)


def test_invalid_candidate_is_never_registered_and_old_close_survives(setup):
    warehouse, publisher, source = setup
    old = canonical(publisher.consume())
    with warehouse.connection() as con:
        con.execute(f"UPDATE {warehouse.schema}.invoice_balances_v2 SET credit_cents=credit_cents+1")
    with pytest.raises(ValueError, match="oracle"):
        publisher.stage("bad", source, CUTOFF)
    with pytest.raises(ValueError, match="Unknown"):
        publisher.promote("bad")
    assert canonical(publisher.consume()) == old


def test_schema_drift_rejected_before_promotion(setup):
    warehouse, publisher, source = setup
    old = canonical(publisher.consume())
    with warehouse.connection() as con:
        con.execute(f"ALTER TABLE {warehouse.schema}.invoice_balances_v2 RENAME credit_cents TO credits")
    with pytest.raises(ValueError, match="column contract"):
        publisher.stage("drift", source, CUTOFF)
    assert canonical(publisher.consume()) == old


def test_tampered_snapshot_cannot_be_promoted(setup):
    warehouse, publisher, source = setup
    old = canonical(publisher.consume())
    publisher.stage("candidate", source, CUTOFF)
    with warehouse.connection() as con:
        con.execute(f"UPDATE {warehouse.schema}_release_candidate.invoice_balances_v2 SET credit_cents=credit_cents+1")
    with pytest.raises(ValueError, match="changed after validation"):
        publisher.promote("candidate")
    assert canonical(publisher.consume()) == old


def test_frozen_source_and_prior_schema_are_not_modified_by_new_build(setup):
    warehouse, publisher, source = setup
    old = canonical(publisher.consume())
    changed = copy.deepcopy(source)
    changed["invoices"][0]["amount_cents"] = 20000
    materialize(warehouse, expected_rows(changed, CUTOFF))
    assert publisher.frozen_source("old") == (source, CUTOFF)
    assert canonical(publisher.consume()) == old
