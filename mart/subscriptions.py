"""Build immutable, knowledge-cutoff subscription reports from synthetic feeds.

No payment processing or revenue recognition: the contract describes a small
analytics model for subscription run rates, billed amounts and collected cash.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
COMMON = {"record_id": "VARCHAR", "revision": "INTEGER", "recorded_at": "TIMESTAMPTZ"}
FEEDS = {
    "subscription_changes": {
        "subscription_id": "VARCHAR", "customer_id": "VARCHAR", "effective_date": "DATE",
        "status": "VARCHAR", "monthly_amount_cents": "BIGINT",
    },
    "invoices": {"customer_id": "VARCHAR", "issued_date": "DATE", "amount_cents": "BIGINT"},
    "payments": {"invoice_id": "VARCHAR", "paid_date": "DATE", "amount_cents": "BIGINT"},
    "refunds": {"payment_id": "VARCHAR", "refunded_date": "DATE", "amount_cents": "BIGINT"},
}
TABLES = {
    "monthly_mrr_bridge": "month", "customer_mrr_bridge": "month, customer_id",
    "monthly_cash_bridge": "month", "invoice_balances": "invoice_id",
    "subscription_intervals": "subscription_id, effective_date",
}
MRR_FIELDS = ["opening_mrr_cents", "new_mrr_cents", "expansion_mrr_cents", "reactivation_mrr_cents",
              "contraction_mrr_cents", "churn_mrr_cents", "closing_mrr_cents"]
EXPORT_FIELDS = {
    "monthly_mrr_bridge": ["month", *MRR_FIELDS],
    "customer_mrr_bridge": ["month", "customer_id", "opening_mrr_cents", "closing_mrr_cents", "previously_paying",
                            "new_mrr_cents", "reactivation_mrr_cents", "expansion_mrr_cents", "contraction_mrr_cents", "churn_mrr_cents"],
    "monthly_cash_bridge": ["month", "opening_balance_cents", "invoiced_cents", "paid_cents", "refunded_cents",
                            "net_cash_cents", "closing_balance_cents"],
    "invoice_balances": ["invoice_id", "customer_id", "invoiced_cents", "paid_cents", "refunded_cents", "balance_cents"],
}
DEFAULT_CUTOFFS = ("2026-04-01T00:00:00Z", "2026-04-03T00:00:00Z", "2026-04-04T00:00:00Z")


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("recorded_at and knowledge cutoff must be UTC timestamp strings")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid UTC timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("All timestamps must explicitly use UTC")
    return parsed


def business_date(value: str) -> date:
    if not isinstance(value, str):
        raise ValueError("Business dates must use YYYY-MM-DD")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("Business dates must use YYYY-MM-DD")
    return result


def validate_source(source: dict) -> dict:
    """Reject malformed contracts and deduplicate identical source deliveries."""
    expected = {"schema_version", "currency", "scope", "complete_subscription_history", *FEEDS}
    if not isinstance(source, dict) or set(source) != expected:
        raise ValueError("Source must contain exactly the documented schema fields")
    if type(source["schema_version"]) is not int or source["schema_version"] != 1:
        raise ValueError("Unsupported source schema version")
    if source["currency"] != "GBP" or source["scope"] != "synthetic":
        raise ValueError("This bounded lab supports explicitly synthetic GBP feeds only")
    if source["complete_subscription_history"] is not True:
        raise ValueError("Full subscription history is required to distinguish new from reactivation")
    normalized = {key: source[key] for key in expected - FEEDS.keys()}
    subscription_customers = {}
    for name, columns in FEEDS.items():
        if not isinstance(source[name], list):
            raise ValueError(f"{name} must be a list")
        identities = {}
        histories = {}
        for row in source[name]:
            schema = COMMON | columns
            if not isinstance(row, dict) or set(row) != set(schema):
                raise ValueError(f"{name}: missing or unexpected record fields")
            for key, kind in schema.items():
                value = row[key]
                if kind in ("INTEGER", "BIGINT"):
                    if type(value) is not int or value < (1 if key == "revision" else 0) or value > 2**53 - 1:
                        raise ValueError(f"{name}.{key}: expected a bounded nonnegative integer")
                elif kind == "DATE":
                    business_date(value)
                elif kind == "TIMESTAMPTZ":
                    timestamp(value)
                elif not isinstance(value, str) or not value.strip() or len(value) > 200:
                    raise ValueError(f"{name}.{key}: expected nonblank text of at most 200 characters")
                if key.endswith("_id") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value):
                    raise ValueError(f"{name}.{key}: identifiers require a letter/digit followed by letters, digits, _, ., : or -")
            if name in ("payments", "refunds") and row["amount_cents"] == 0:
                raise ValueError(f"{name}: zero cash movement is invalid")
            if name == "subscription_changes":
                if row["status"] not in ("active", "cancelled"):
                    raise ValueError("Unknown subscription status")
                if (row["status"] == "active") != (row["monthly_amount_cents"] > 0):
                    raise ValueError("Active subscriptions need positive MRR; cancelled subscriptions need zero MRR")
                sid, cid = row["subscription_id"], row["customer_id"]
                if sid in subscription_customers and subscription_customers[sid] != cid:
                    raise ValueError("A subscription cannot change customer ownership in this contract")
                subscription_customers[sid] = cid
            identity = row["record_id"], row["revision"]
            if identity in identities and identities[identity] != row:
                raise ValueError(f"{name}: conflicting duplicate {identity}")
            identities[identity] = dict(row)
        for row in identities.values():
            histories.setdefault(row["record_id"], []).append(row)
        for history in histories.values():
            history.sort(key=lambda item: item["revision"])
            if [row["revision"] for row in history] != list(range(1, len(history) + 1)):
                raise ValueError(f"{name}: revisions must be contiguous from 1")
            times = [timestamp(row["recorded_at"]) for row in history]
            if any(right <= left for left, right in zip(times, times[1:], strict=False)):
                raise ValueError(f"{name}: revisions must have strictly increasing recorded_at")
            # A correction can change values/dates, never the entity to which an event belongs.
            keys = {"subscription_changes": ("subscription_id", "customer_id"), "invoices": ("customer_id",),
                    "payments": ("invoice_id",), "refunds": ("payment_id",)}[name]
            if any(any(row[key] != history[0][key] for key in keys) for row in history):
                raise ValueError(f"{name}: revisions cannot move an event between entities")
        normalized[name] = sorted(identities.values(), key=lambda row: (row["record_id"], row["revision"]))
    return normalized


def select_snapshot(source: dict, cutoff: datetime) -> dict:
    snapshot = {}
    for name in FEEDS:
        records = {}
        for row in source[name]:
            if timestamp(row["recorded_at"]) <= cutoff:
                records[row["record_id"]] = row
        snapshot[name] = records
    positions = set()
    for row in snapshot["subscription_changes"].values():
        key = row["subscription_id"], row["effective_date"]
        if key in positions:
            raise ValueError("Ambiguous simultaneous subscription changes require one versioned logical event")
        positions.add(key)
    for payment in snapshot["payments"].values():
        invoice = snapshot["invoices"].get(payment["invoice_id"])
        if invoice is None or payment["paid_date"] < invoice["issued_date"]:
            raise ValueError("A payment must reference a visible invoice and cannot precede its issue date")
    refunded = {}
    for refund in snapshot["refunds"].values():
        payment = snapshot["payments"].get(refund["payment_id"])
        if payment is None or refund["refunded_date"] < payment["paid_date"]:
            raise ValueError("A refund must reference a visible payment and cannot precede its paid date")
        refunded[payment["record_id"]] = refunded.get(payment["record_id"], 0) + refund["amount_cents"]
        if refunded[payment["record_id"]] > payment["amount_cents"]:
            raise ValueError("Refunds exceed the referenced payment")
    return snapshot


def serial(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def rows(con, table: str, order: str) -> list[dict]:
    projection = "* REPLACE (CAST(recorded_at AS VARCHAR) AS recorded_at)" if table == "subscription_intervals" else "*"
    result = con.execute(f"SELECT {projection} FROM {table} ORDER BY {order}")
    keys = [column[0] for column in result.description]
    output = [{key: serial(value) for key, value in zip(keys, values, strict=True)} for values in result.fetchall()]
    if any(abs(value) > 2**53 - 1 for row in output for key, value in row.items() if key.endswith("_cents")):
        raise ValueError("Derived monetary amount exceeds exact browser integer range")
    return output


def assert_checks(report: dict, snapshot: dict) -> list[dict]:
    """Reconcile SQL results to independent selected-source totals and identities."""
    checks = []

    def check(name, actual, expected):
        if actual != expected:
            raise ValueError(f"{name}: expected {expected}, found {actual}")
        checks.append({"check": name, "passed": True, "actual": actual, "expected": expected})

    for table in ("monthly_mrr_bridge", "customer_mrr_bridge"):
        violations = sum(
            row["opening_mrr_cents"] + row["new_mrr_cents"] + row["expansion_mrr_cents"]
            + row["reactivation_mrr_cents"] - row["contraction_mrr_cents"] - row["churn_mrr_cents"]
            != row["closing_mrr_cents"] for row in report[table]
        )
        check(f"{table}: bridge identity violations", violations, 0)
        keys = [(row["month"], row.get("customer_id")) for row in report[table]]
        check(f"{table}: unique grain", len(set(keys)), len(keys))
    cash = report["monthly_cash_bridge"]
    violations = sum(row["opening_balance_cents"] + row["invoiced_cents"] - row["net_cash_cents"]
                     != row["closing_balance_cents"] for row in cash)
    check("monthly_cash_bridge: balance identity violations", violations, 0)
    start, end = report["period_start"], report["period_end"]
    for name, field, metric in (("invoices", "issued_date", "invoiced_cents"),
                                ("payments", "paid_date", "paid_cents"), ("refunds", "refunded_date", "refunded_cents")):
        expected = sum(row["amount_cents"] for row in snapshot[name].values() if start <= row[field] < end)
        check(f"{name}: monthly totals versus selected source", sum(row[metric] for row in cash), expected)
    check("invoice balances versus closing cash bridge", sum(row["balance_cents"] for row in report["invoice_balances"]),
          cash[-1]["closing_balance_cents"])
    check("invoice balances: unique grain", len({row["invoice_id"] for row in report["invoice_balances"]}),
          len(report["invoice_balances"]))
    # An independently selected last effective state, rather than the SQL intervals.
    latest = {}
    for row in snapshot["subscription_changes"].values():
        sid = row["subscription_id"]
        if row["effective_date"] < end and (sid not in latest or row["effective_date"] > latest[sid]["effective_date"]):
            latest[sid] = row
    check("closing MRR versus latest source states", report["monthly_mrr_bridge"][-1]["closing_mrr_cents"],
          sum(row["monthly_amount_cents"] for row in latest.values()))
    return checks


def build_report(source: dict, as_of: str, start: str = "2026-01-01", end: str = "2026-04-01") -> dict:
    source = validate_source(source)
    cutoff, period_start, period_end = timestamp(as_of), business_date(start), business_date(end)
    if period_start.day != 1 or period_end.day != 1 or period_start >= period_end or period_end > cutoff.date():
        raise ValueError("Report must cover complete months [start, end), ending no later than the knowledge cutoff")
    if (period_end.year - period_start.year) * 12 + period_end.month - period_start.month > 120:
        raise ValueError("This bounded report supports at most 120 months")
    snapshot = select_snapshot(source, cutoff)
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        con.execute("CREATE TABLE report_parameters (knowledge_cutoff TIMESTAMPTZ, period_start DATE, period_end DATE)")
        con.execute("INSERT INTO report_parameters VALUES (?, ?, ?)", [cutoff, period_start, period_end])
        for name, columns in FEEDS.items():
            schema = COMMON | columns
            con.execute(f"CREATE TABLE {name}_raw ({', '.join(f'{key} {kind}' for key, kind in schema.items())})")
            if source[name]:
                con.executemany(f"INSERT INTO {name}_raw VALUES ({', '.join('?' for _ in schema)})",
                                [[row[key] for key in schema] for row in source[name]])
        con.execute((ROOT / "sql/70_subscriptions.sql").read_text(encoding="utf-8"))
        report = {"knowledge_cutoff": cutoff.isoformat(), "period_start": start, "period_end": end,
                  "currency": "GBP", "unit": "integer pence", "scope": "synthetic",
                  "selected_source_records": {name: len(records) for name, records in snapshot.items()},
                  **{table: rows(con, table, order) for table, order in TABLES.items()}}
    report["checks"] = assert_checks(report, snapshot)
    return report


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def publish(source_path: Path, output: Path, cutoffs=DEFAULT_CUTOFFS, start="2026-01-01", end="2026-04-01") -> dict:
    """Publish a new directory after all reports pass; never replace an existing release."""
    from mart.subscription_report import render

    if output.exists():
        raise FileExistsError(f"Immutable release already exists: {output}")
    if not cutoffs or len({timestamp(cutoff) for cutoff in cutoffs}) != len(cutoffs):
        raise ValueError("Provide at least one unique knowledge cutoff")
    cutoffs = sorted(cutoffs, key=timestamp)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".subscriptions-", dir=output.parent))
    try:
        # Copy before reading: all artifacts are derived from these exact frozen bytes.
        shutil.copyfile(source_path, staging / "source.json")
        source = json.loads((staging / "source.json").read_text(encoding="utf-8"))
        reports = [build_report(source, cutoff, start, end) for cutoff in cutoffs]
        bundle = {"schema_version": 1, "scope": "Synthetic subscription analytics exercise; not commercial operating evidence.",
                  "source_sha256": digest(staging / "source.json"), "reports": reports}
        write_json(staging / "report.json", bundle)
        for table in ("monthly_mrr_bridge", "monthly_cash_bridge", "customer_mrr_bridge", "invoice_balances"):
            exported = [{"knowledge_cutoff": report["knowledge_cutoff"], **row} for report in reports for row in report[table]]
            with (staging / f"{table}.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["knowledge_cutoff", *EXPORT_FIELDS[table]])
                writer.writeheader()
                writer.writerows(exported)
        (staging / "index.html").write_text(render(bundle), encoding="utf-8")
        code = ["mart/subscriptions.py", "mart/subscription_report.py", "sql/70_subscriptions.sql"]
        # Raw source hashes identify the executed bytes even with uncommitted local changes.
        manifest = {"schema_version": 1, "source_sha256": bundle["source_sha256"],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "base_git_revision": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                                        text=True, check=True).stdout.strip(),
                    "runtime": {"python": platform.python_version(), "duckdb": duckdb.__version__},
                    "executed_code": [{"path": name, "sha256": digest(ROOT / name)} for name in code],
                    "checks_passed": sum(len(report["checks"]) for report in reports),
                    "artifacts": [{"path": path.name, "sha256": digest(path), "bytes": path.stat().st_size}
                                  for path in sorted(staging.iterdir())]}
        write_json(staging / "manifest.json", manifest)
        staging.rename(output)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "subscriptions/demo.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", action="append", help="UTC knowledge cutoff; repeat to compare restatements")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-04-01")
    args = parser.parse_args()
    manifest = publish(args.source, args.output, args.as_of or DEFAULT_CUTOFFS, args.start, args.end)
    print(json.dumps({"output": str(args.output), "checks_passed": manifest["checks_passed"],
                      "source_sha256": manifest["source_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
