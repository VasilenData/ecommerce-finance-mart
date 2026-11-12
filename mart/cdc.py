"""A bounded, single-partition Debezium sink, separate from the UCI finance mart.

The transaction owns both the current order state and the consumed Kafka offset.
It provides replay-safe application to this database, not a global exactly-once claim.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Callable

import duckdb

TOPIC = "finance.public.orders"
FIELDS = {"order_id", "customer_id", "order_day", "gross_cents", "refund_cents", "status"}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def initialize(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        create table if not exists cdc_events (
            topic varchar, partition_id integer, offset_id bigint, digest varchar,
            operation varchar, event_json varchar,
            primary key (topic, partition_id, offset_id));
        create table if not exists cdc_offsets (
            topic varchar, partition_id integer, offset_id bigint,
            primary key (topic, partition_id));
        create table if not exists operational_orders (
            order_id integer primary key, customer_id integer not null,
            order_day date not null, gross_cents bigint not null,
            refund_cents bigint not null, status varchar not null);
        create table if not exists operational_daily (
            order_day date primary key, paid_orders bigint,
            gross_cents hugeint, refund_cents hugeint, net_cents hugeint);
    """)


def last_offset(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("select offset_id from cdc_offsets where topic=? and partition_id=0", [TOPIC]).fetchone()
    return -1 if row is None else row[0]


def validate_row(row: dict, key: dict) -> tuple:
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError("Source contract changed: exact order fields required")
    if not isinstance(key, dict) or set(key) != {"order_id"} or row["order_id"] != key["order_id"]:
        raise ValueError("Order key and row disagree")
    for name in ["order_id", "customer_id", "gross_cents", "refund_cents"]:
        if type(row[name]) is not int:
            raise ValueError(f"{name} must be an integer")
    if row["order_id"] <= 0 or row["customer_id"] <= 0:
        raise ValueError("Order/customer identities must be positive")
    if not 0 <= row["refund_cents"] <= row["gross_cents"] <= 10**12:
        raise ValueError("Refund must be between zero and gross; amounts are bounded integer GBP pence")
    if row["status"] not in {"paid", "cancelled"}:
        raise ValueError("Unknown order status")
    day = date.fromisoformat(row["order_day"])
    if day.isoformat() != row["order_day"]:
        raise ValueError("order_day must be ISO YYYY-MM-DD")
    return tuple(row[name] for name in ["order_id", "customer_id", "order_day", "gross_cents", "refund_cents", "status"])


def apply_batch(con: duckdb.DuckDBPyConnection, records: list[dict], before_commit: Callable | None = None) -> dict:
    """Commit one ordered batch or preserve all previous state on any failure.

    Accept only one uncompacted topic partition, starting from offset zero in a new
    database. Existing offsets may be replayed with identical bytes/JSON content.
    Unknown schemas, wrong streams and offset gaps stop the batch rather than skip data.
    """
    applied = duplicates = 0
    con.execute("begin")
    try:
        for record in records:
            if record["topic"] != TOPIC or record["partition"] != 0:
                raise ValueError("Only the declared orders topic and partition zero are supported")
            offset = record["offset"]
            if type(offset) is not int or offset < 0:
                raise ValueError("Invalid Kafka offset")
            fingerprint = hashlib.sha256(canonical({"key": record["key"], "value": record["value"]}).encode()).hexdigest()
            prior = con.execute("select digest from cdc_events where topic=? and partition_id=0 and offset_id=?",
                                [TOPIC, offset]).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ValueError("An existing Kafka offset has different contents")
                duplicates += 1
                continue
            if offset != last_offset(con) + 1:
                raise ValueError("Offset gap or history truncation: rebuild from a complete source snapshot")
            key, value = record["key"], record["value"]
            if not isinstance(key, dict) or set(key) != {"order_id"} or type(key["order_id"]) is not int or key["order_id"] <= 0:
                raise ValueError("A positive integer order key is required")
            if value is None:
                operation = "tombstone"  # delete was applied by the preceding Debezium d event
            else:
                if not isinstance(value, dict) or value.get("source", {}).get("table") != "orders":
                    raise ValueError("Expected a Debezium orders envelope")
                operation = value.get("op")
                if operation in {"r", "c", "u"}:
                    row = validate_row(value.get("after"), key)
                    con.execute("insert or replace into operational_orders values (?, ?, ?, ?, ?, ?)", row)
                elif operation == "d":
                    validate_row(value.get("before"), key)
                    if value.get("after") is not None:
                        raise ValueError("Delete envelope must have null after")
                    con.execute("delete from operational_orders where order_id=?", [key["order_id"]])
                else:
                    raise ValueError("Unsupported Debezium operation; truncate is deliberately rejected")
            con.execute("insert into cdc_events values (?, 0, ?, ?, ?, ?)",
                        [TOPIC, offset, fingerprint, operation, canonical(record)])
            con.execute("insert or replace into cdc_offsets values (?, 0, ?)", [TOPIC, offset])
            applied += 1
        con.execute("""
            delete from operational_daily;
            insert into operational_daily
            select order_day, count(*), sum(gross_cents), sum(refund_cents), sum(gross_cents-refund_cents)
            from operational_orders where status='paid' group by order_day;
        """)
        expected = con.execute("select coalesce(sum(gross_cents-refund_cents),0) from operational_orders where status='paid'").fetchone()[0]
        observed = con.execute("select coalesce(sum(net_cents),0) from operational_daily").fetchone()[0]
        if expected != observed:
            raise ValueError("Order-to-mart reconciliation failed")
        if before_commit:
            before_commit()
        con.execute("commit")
    except BaseException:
        con.execute("rollback")
        raise
    return {"applied": applied, "duplicates": duplicates, "last_offset": last_offset(con)}


def state(con: duckdb.DuckDBPyConnection) -> dict:
    """Stable JSON-compatible state for independent source reconciliation and replay."""
    orders = con.execute("select * from operational_orders order by order_id").fetchall()
    daily = con.execute("select * from operational_daily order by order_day").fetchall()
    return {
        "orders": [[v.isoformat() if isinstance(v, date) else v for v in row] for row in orders],
        "daily": [[v.isoformat() if isinstance(v, date) else v for v in row] for row in daily],
        "last_offset": last_offset(con),
        "events": con.execute("select count(*) from cdc_events").fetchone()[0],
    }
