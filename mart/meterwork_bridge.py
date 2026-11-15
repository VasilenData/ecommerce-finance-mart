"""Receive complete Meterwork snapshots into an isolated synthetic USD reporting domain.

This module imports no Meterwork code. It independently rates exported source
records and preserves old consumer releases after rejected deliveries.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

CONTRACT = "meterwork-finance/v1"
FILES = {"accounts.json", "statements.json", "source_snapshots.json"}
METADATA = {"id", "revision", "created_at", "known_at", "content_sha256"}
MAX_FILE_BYTES = 2_000_000


class Rejected(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


class StoredReleaseError(RuntimeError):
    """A persisted release failed its read contract; do not serve or overwrite it."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def require(condition, code, detail):
    if not condition:
        raise Rejected(code, detail)


def utc(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.tzinfo is not None and result.utcoffset().total_seconds() == 0, "timestamp", "Explicit UTC timestamps are required")
        return result
    except (ValueError, TypeError, AttributeError) as error:
        raise Rejected("timestamp", "Invalid UTC timestamp") from error


def integer(value, *, positive=False):
    require(type(value) is int and abs(value) <= 9_000_000_000_000_000, "integer_money", "Amounts and counts must be bounded integers")
    if positive:
        require(value > 0, "integer_money", "A positive integer is required")
    return value


def invoice_rounding(amount):
    require(integer(amount) >= 0, "rounding", "Only nonnegative complete statement amounts are supported")
    cents = (amount + 5000) // 10000
    return cents, amount - cents * 10000


def unique(rows, key, name):
    require(isinstance(rows, list) and len(rows) <= 5000, "shape", f"Invalid bounded {name} array")
    result = {}
    for row in rows:
        identity = key(row)
        require(identity not in result, "duplicate_identity", f"Duplicate {name} identity")
        result[identity] = row
    return result


def load_json(path):
    try:
        require(path.is_file() and not path.is_symlink(), "missing_delivery", f"Missing regular file: {path.name}")
        require(path.stat().st_size <= MAX_FILE_BYTES, "payload_bound", "Delivery file exceeds 2 MB")
        data = path.read_bytes()

        # Duplicate JSON object keys are errors, rather than silently keeping the last value.
        def pairs(items):
            result = {}
            for key, value in items:
                require(key not in result, "duplicate_json_key", "Duplicate JSON object key")
                result[key] = value
            return result

        def invalid_constant(_value):
            raise Rejected("invalid_json_number", "Non-finite JSON numbers are not allowed")

        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant), data
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Rejected("unreadable_delivery", f"Cannot read {path.name}") from error


def rerate(statement, snapshot):
    """Independent arithmetic from raw source; no producer rating functions imported."""
    account, month = statement["tenant_id"], statement["month"]
    cutoff = utc(statement["known_at"])
    require(
        statement["currency"] == "USD" and statement["status"] == "estimate" and statement["engine_version"] == "1.1.0",
        "statement_contract",
        "Only USD estimate engine 1.1.0 is supported",
    )
    integer(statement["revision"], positive=True)
    require(utc(statement["created_at"]) == cutoff, "statement_contract", "Publication and known-at clocks disagree")
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError) as error:
        raise Rejected("month", "Month must be YYYY-MM") from error
    require(start.strftime("%Y-%m") == month, "month", "Month must be canonical YYYY-MM")
    end = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    events = snapshot["events"]
    prices = snapshot["prices"]
    all_events = unique(events, lambda row: (row["source"], row["id"]), "source event")
    unique(prices, lambda row: row["id"], "price")
    unique(prices, lambda row: utc(row["effective_from"]), "price boundary")
    require(all(row["tenant_id"] == account for row in events + prices), "account_isolation", "Cross-account source row")
    for event in all_events.values():
        integer(event["units"])
        require(event["units"] != 0, "event_units", "Zero-unit event")
        body = {key: event[key] for key in ("source", "id", "occurred_at", "units", "correction_of")}
        body["occurred_at"] = utc(body["occurred_at"]).isoformat()
        require(sha(body) == event["payload_sha256"], "source_fingerprint", "Event payload fingerprint differs")
        utc(event["received_at"])
    for price in prices:
        require(integer(price["unit_price_microusd"]) >= 0, "price", "Negative price")
        utc(price["recorded_at"])
    selected = {key: row for key, row in all_events.items() if utc(row["received_at"]) <= cutoff}
    known_prices = sorted([row for row in prices if utc(row["recorded_at"]) <= cutoff], key=lambda row: utc(row["effective_from"]))
    balances, expected = defaultdict(int), []
    for _key, event in sorted(selected.items()):
        original = selected.get((event["source"], event["correction_of"])) if event["correction_of"] else event
        require(original is not None and not original["correction_of"], "correction", "Correction has no known original")
        require(utc(original["occurred_at"]) == utc(event["occurred_at"]), "correction", "Correction changed occurrence time")
        require(bool(event["correction_of"]) or event["units"] > 0, "correction", "Original usage cannot be negative")
        balances[(event["source"], original["id"])] += event["units"]
        occurred = utc(original["occurred_at"])
        if not start <= occurred < end:
            continue
        candidates = [price for price in known_prices if utc(price["effective_from"]) <= occurred]
        require(bool(candidates), "unpriced_usage", "No known price for source event")
        price = candidates[-1]
        expected.append(
            {
                "source": event["source"],
                "event_id": event["id"],
                "occurred_at": occurred.isoformat(),
                "units": event["units"],
                "correction_of": event["correction_of"],
                "price_version": price["id"],
                "unit_price_microusd": price["unit_price_microusd"],
                "amount_microusd": event["units"] * price["unit_price_microusd"],
            }
        )
    require(all(value >= 0 for value in balances.values()), "correction", "Corrections make original usage negative")
    unique(statement["lines"], lambda row: (row["source"], row["event_id"]), "statement line")
    for line in statement["lines"]:
        for field in ("units", "unit_price_microusd", "amount_microusd"):
            integer(line[field])
    require(statement["lines"] == expected, "rating_mismatch", "Statement lines differ from independent source rating")
    amount = sum(row["amount_microusd"] for row in expected)
    units = sum(row["units"] for row in expected)
    require(
        integer(statement["amount_microusd"]) == amount and amount >= 0 and integer(statement["total_units"]) == units,
        "rating_mismatch",
        "Statement totals differ",
    )
    used_prices = sorted({row["price_version"] for row in expected})
    require(statement["pricing_version_ids"] == used_prices, "price", "Price lineage differs")
    source = {
        "events": [row for _, row in sorted(selected.items()) if start <= utc(row["occurred_at"]) < end],
        "prices": [row for row in known_prices if row["id"] in used_prices],
    }
    require(sha(source) == statement["source_sha256"], "source_fingerprint", "Source snapshot fingerprint differs")
    require(
        sha({key: value for key, value in statement.items() if key not in METADATA}) == statement["content_sha256"],
        "statement_fingerprint",
        "Statement content fingerprint differs",
    )
    cents, residual = invoice_rounding(amount)
    return {
        "account_id": account,
        "invoice_id": f"meterwork:{account}:{month}",
        "month": month,
        "revision": statement["revision"],
        "statement_id": statement["id"],
        "known_at": statement["known_at"],
        "total_units": units,
        "amount_microusd": amount,
        "invoice_cents": cents,
        "rounding_residual_microusd": residual,
        "statement_sha256": statement["content_sha256"],
        "source_sha256": statement["source_sha256"],
    }


def decode_bundle(directory, expected_hash):
    directory = Path(directory)
    manifest, _ = load_json(directory / "manifest.json")
    require(manifest["contract_version"] == CONTRACT and manifest["scope"] == "synthetic-local", "contract", "Unsupported source contract")
    require(
        manifest["currency"] == "USD"
        and manifest["statement_money_unit"] == "microUSD"
        and manifest["invoice_money_unit"] == "USD_cent"
        and manifest["rounding"] == "statement_total_half_up",
        "units",
        "Currency, units or rounding contract differs",
    )
    require(
        sha({key: value for key, value in manifest.items() if key != "bundle_sha256"}) == manifest["bundle_sha256"] == expected_hash,
        "bundle_fingerprint",
        "Manifest differs from the expected producer delivery",
    )
    require(set(manifest["files"]) == FILES, "file_contract", "Missing or unexpected source file")
    integer(manifest["export_sequence"], positive=True)
    require(isinstance(manifest["stream_id"], str) and 0 < len(manifest["stream_id"]) <= 128, "stream", "Invalid stream identity")
    payload = {}
    for name in sorted(FILES):
        rows, data = load_json(directory / name)
        require(sha(data) == manifest["files"][name]["sha256"], "file_fingerprint", f"Source file hash differs: {name}")
        integer(manifest["files"][name]["rows"])
        require(
            isinstance(rows, list) and len(rows) == manifest["files"][name]["rows"] <= 5000,
            "file_count",
            f"Source row count differs: {name}",
        )
        payload[name] = rows
    accounts = unique(payload["accounts.json"], lambda row: row["account_id"], "account")
    require(bool(accounts), "account_scope", "Empty account scope")
    require(
        all(
            set(row) == {"account_id", "customer_id"} and all(isinstance(v, str) and 0 < len(v) <= 128 for v in row.values())
            for row in accounts.values()
        ),
        "account_scope",
        "Account mapping has unexpected fields or identifiers",
    )
    unique(payload["accounts.json"], lambda row: row["customer_id"], "customer")
    snapshots = unique(payload["source_snapshots.json"], lambda row: row["account_id"], "snapshot account")
    require(set(snapshots) == set(accounts), "account_scope", "Snapshot/account coverage differs")
    statements = unique(payload["statements.json"], lambda row: (row["tenant_id"], row["month"], row["revision"]), "statement revision")
    unique(payload["statements.json"], lambda row: row["id"], "statement UUID")
    scope = unique(manifest["expected_account_months"], lambda row: (row["account_id"], row["month"]), "account-month scope")
    actual_scope = defaultdict(list)
    rated = []
    for (account, month, revision), statement in sorted(statements.items()):
        require(account in accounts, "account_scope", "Unmapped statement account")
        actual_scope[(account, month)].append(revision)
        rated.append({**rerate(statement, snapshots[account]), "customer_id": accounts[account]["customer_id"]})
    require(
        set(actual_scope) == set(scope) and {key[0] for key in scope} == set(accounts),
        "incomplete_scope",
        "Account/month delivery is incomplete",
    )
    for key, expected in scope.items():
        count = integer(expected["revision_count"], positive=True)
        require(actual_scope[key] == list(range(1, count + 1)), "revision_gap", "Statement revisions must be complete and contiguous")
        clocks = [utc(statements[(*key, revision)]["known_at"]) for revision in range(1, count + 1)]
        require(clocks == sorted(clocks), "source_cutoff", "Revision knowledge must not move backwards")
    for account, snapshot in snapshots.items():
        cutoff = utc(snapshot["source_cutoff_utc"])
        require(
            cutoff == max(utc(row["known_at"]) for row in statements.values() if row["tenant_id"] == account),
            "source_cutoff",
            "Account cutoff differs from its selected statements",
        )
        require(
            all(utc(row["received_at"]) <= cutoff for row in snapshot["events"])
            and all(utc(row["recorded_at"]) <= cutoff for row in snapshot["prices"]),
            "source_cutoff",
            "Snapshot contains source rows beyond its account cutoff",
        )
    require(
        bool(statements) and utc(manifest["source_cutoff_utc"]) == max(utc(row["known_at"]) for row in statements.values()),
        "source_cutoff",
        "Export cutoff differs from selected statement knowledge",
    )
    return manifest, payload, rated


def cash_fixture(accounts):
    """Declared simulation, not Meterwork payment processing or real collections."""
    identities = {row["customer_id"]: f"meterwork:{row['account_id']}:2026-10" for row in accounts}
    return {
        "schema_version": 1,
        "scope": "synthetic-finance-collections",
        "currency": "USD",
        "knowledge_cutoff_utc": "2026-11-02T00:00:00Z",
        "payments": [
            {
                "payment_id": "payment-a",
                "invoice_id": identities["customer_a"],
                "amount_cents": 2000,
                "recorded_at": "2026-11-01T00:00:00Z",
            },
            {
                "payment_id": "payment-b",
                "invoice_id": identities["customer_b"],
                "amount_cents": 2500,
                "recorded_at": "2026-11-01T00:00:00Z",
            },
        ],
        "refunds": [{"refund_id": "refund-b", "payment_id": "payment-b", "amount_cents": 200, "recorded_at": "2026-11-01T12:00:00Z"}],
    }


def validated_cash(cash, invoices):
    require(
        cash["schema_version"] == 1 and cash["scope"] == "synthetic-finance-collections" and cash["currency"] == "USD",
        "cash_contract",
        "Only the separate synthetic USD cash contract is accepted",
    )
    cutoff = utc(cash["knowledge_cutoff_utc"])
    payments = unique(cash["payments"], lambda row: row["payment_id"], "payment")
    refunds = unique(cash["refunds"], lambda row: row["refund_id"], "refund")
    amounts = defaultdict(lambda: {"paid_cents": 0, "refunded_cents": 0})
    refunded = defaultdict(int)
    for row in payments.values():
        integer(row["amount_cents"], positive=True)
        require(row["invoice_id"] in invoices, "cash_reference", "Payment references an unknown invoice")
        if utc(row["recorded_at"]) <= cutoff:
            amounts[row["invoice_id"]]["paid_cents"] += row["amount_cents"]
    for row in refunds.values():
        integer(row["amount_cents"], positive=True)
        require(row["payment_id"] in payments, "cash_reference", "Refund references an unknown payment")
        payment = payments[row["payment_id"]]
        require(utc(row["recorded_at"]) >= utc(payment["recorded_at"]), "cash_clock", "Refund precedes payment knowledge")
        refunded[row["payment_id"]] += row["amount_cents"]
        if utc(row["recorded_at"]) <= cutoff:
            amounts[payment["invoice_id"]]["refunded_cents"] += row["amount_cents"]
    require(all(value <= payments[key]["amount_cents"] for key, value in refunded.items()), "cash_reference", "Refunds exceed payment")
    for amount in amounts.values():
        integer(amount["paid_cents"])
        integer(amount["refunded_cents"])
    return amounts


def revision_changes(statements):
    """Explain line-level change independently from the producer's difference report."""
    groups = defaultdict(list)
    for statement in statements:
        groups[(statement["tenant_id"], statement["month"])].append(statement)
    changes = []
    for (account, month), revisions in sorted(groups.items()):
        revisions.sort(key=lambda row: row["revision"])
        for before, after in zip(revisions, revisions[1:], strict=False):
            old = {(row["source"], row["event_id"]): row for row in before["lines"]}
            new = {(row["source"], row["event_id"]): row for row in after["lines"]}
            lines = []
            for key in sorted(set(old) | set(new)):
                previous, current = old.get(key), new.get(key)
                delta = (current["amount_microusd"] if current else 0) - (previous["amount_microusd"] if previous else 0)
                if not delta:
                    continue
                reason = ("usage_correction" if current and current["correction_of"] else "late_usage") if previous is None else "repricing"
                lines.append(
                    {
                        "source": key[0],
                        "event_id": key[1],
                        "reason": reason,
                        "delta_microusd": delta,
                        "correction_of": current["correction_of"] if current else None,
                    }
                )
            amount_delta = after["amount_microusd"] - before["amount_microusd"]
            require(sum(row["delta_microusd"] for row in lines) == amount_delta, "change_bridge", "Revision change does not reconcile")
            old_cents, old_residual = invoice_rounding(before["amount_microusd"])
            new_cents, new_residual = invoice_rounding(after["amount_microusd"])
            changes.append(
                {
                    "account_id": account,
                    "month": month,
                    "before_revision": before["revision"],
                    "after_revision": after["revision"],
                    "before_statement_id": before["id"],
                    "after_statement_id": after["id"],
                    "delta_microusd": amount_delta,
                    "invoice_delta_cents": new_cents - old_cents,
                    "residual_delta_microusd": new_residual - old_residual,
                    "lines": lines,
                }
            )
    return changes


class Bridge:
    def __init__(self, path):
        self.path = str(path)
        with duckdb.connect(self.path) as con:
            con.execute("CREATE SCHEMA IF NOT EXISTS meterwork_usd")
            con.execute(
                "CREATE TABLE IF NOT EXISTS meterwork_usd.deliveries (bundle_sha VARCHAR PRIMARY KEY, stream_id VARCHAR, "
                "sequence BIGINT, cash_sha VARCHAR, payload_json VARCHAR, report_json VARCHAR, report_sha VARCHAR)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS meterwork_usd.active (singleton INTEGER PRIMARY KEY CHECK(singleton=1), bundle_sha VARCHAR)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS meterwork_usd.exceptions (observed_at TIMESTAMPTZ, expected_bundle_sha VARCHAR, "
                "code VARCHAR, detail VARCHAR, preserved_report_sha VARCHAR)"
            )

    @staticmethod
    def _checked_report(row):
        """Verify a stored logical report against its accepted digest and identity.

        This detects accidental storage drift. The digest shares the database trust
        boundary and does not protect against an administrator rewriting both.
        """
        bundle_hash, sequence, cash_hash, raw, expected = row
        try:
            report = json.loads(raw)
            valid = (
                isinstance(report, dict)
                and sha(report) == expected
                and report["bundle_sha256"] == bundle_hash
                and type(report["sequence"]) is int
                and report["sequence"] == sequence
                and report["cash_sha256"] == cash_hash
            )
        except (TypeError, ValueError, KeyError, OverflowError) as error:
            raise StoredReleaseError("Stored release is not a valid report") from error
        if not valid:
            raise StoredReleaseError("Stored release fingerprint or identity differs; restore a verified backup")
        return report

    def current(self, con=None):
        if con is None:
            with duckdb.connect(self.path, read_only=True) as connection:
                return self.current(connection)
        row = con.execute(
            "SELECT a.bundle_sha,d.sequence,d.cash_sha,d.report_json,d.report_sha "
            "FROM meterwork_usd.active a LEFT JOIN meterwork_usd.deliveries d ON d.bundle_sha=a.bundle_sha"
        ).fetchone()
        if row is None and con.execute("SELECT count(*) FROM meterwork_usd.deliveries").fetchone()[0]:
            raise StoredReleaseError("Retained releases exist without an active pointer")
        return self._checked_report(row) if row else None

    def release(self, bundle_hash):
        with duckdb.connect(self.path, read_only=True) as con:
            row = con.execute(
                "SELECT bundle_sha,sequence,cash_sha,report_json,report_sha FROM meterwork_usd.deliveries WHERE bundle_sha=?", [bundle_hash]
            ).fetchone()
            return self._checked_report(row) if row else None

    def audit(self):
        """Check every retained report, including history that is no longer active."""
        with duckdb.connect(self.path, read_only=True) as con:
            con.execute("BEGIN")
            rows = con.execute(
                "SELECT bundle_sha,sequence,cash_sha,report_json,report_sha FROM meterwork_usd.deliveries ORDER BY sequence"
            ).fetchall()
            reports = [self._checked_report(row) for row in rows]
            current = self.current(con)
            con.execute("COMMIT")
        return {
            "verified_releases": len(reports),
            "active_bundle_sha256": current["bundle_sha256"] if current else None,
            "report_sha256": {row["bundle_sha256"]: sha(row) for row in reports},
        }

    def receive(self, directory, cash, expected_hash, *, fail_after_insert=False, _after_insert=None, _after_promote=None):
        prior = self.current()
        try:
            manifest, payload, rated = decode_bundle(directory, expected_hash)
            latest = {}
            for row in rated:
                latest[row["invoice_id"]] = row
            amounts = validated_cash(cash, latest)
            cash_hash = sha(cash)
            with duckdb.connect(self.path) as con:
                con.execute("BEGIN")
                try:
                    duplicate = con.execute(
                        "SELECT bundle_sha,sequence,cash_sha,report_json,report_sha FROM meterwork_usd.deliveries WHERE bundle_sha=?",
                        [expected_hash],
                    ).fetchone()
                    if duplicate:
                        self._checked_report(duplicate)
                        require(duplicate[2] == cash_hash, "immutable_cash", "Cash snapshot changed for an existing delivery")
                        con.execute("COMMIT")
                        return {
                            "status": "replayed",
                            "bundle_sha256": expected_hash,
                            "original_report_sha256": duplicate[4],
                            "active_report_sha256": sha(prior) if prior else None,
                        }
                    active = con.execute(
                        "SELECT d.stream_id,d.sequence,d.bundle_sha,d.payload_json,d.cash_sha FROM meterwork_usd.active a "
                        "JOIN meterwork_usd.deliveries d ON d.bundle_sha=a.bundle_sha"
                    ).fetchone()
                    if active:
                        require(
                            manifest["stream_id"] == active[0]
                            and manifest["export_sequence"] == active[1] + 1
                            and manifest["previous_bundle_sha256"] == active[2],
                            "delivery_gap",
                            "Delivery is out of order or has a missing predecessor",
                        )
                        previous = json.loads(active[3])
                        require(active[4] == cash_hash, "immutable_cash", "This bounded comparison requires the same cash snapshot")
                        old_statements = {row["id"]: row for row in previous["statements.json"]}
                        new_statements = {row["id"]: row for row in payload["statements.json"]}
                        require(
                            all(new_statements.get(key) == value for key, value in old_statements.items()),
                            "historical_rewrite",
                            "Cumulative delivery removed or changed an old statement",
                        )
                        require(
                            previous["accounts.json"] == payload["accounts.json"],
                            "account_scope",
                            "This bounded stream must preserve its account map",
                        )
                        new_snapshots = {row["account_id"]: row for row in payload["source_snapshots.json"]}
                        for old in previous["source_snapshots.json"]:
                            new = new_snapshots[old["account_id"]]
                            require(
                                utc(new["source_cutoff_utc"]) >= utc(old["source_cutoff_utc"]),
                                "source_cutoff",
                                "Account knowledge moved backwards",
                            )
                            for collection, keys in (("events", ("source", "id")), ("prices", ("id",))):
                                new_rows = {tuple(row[key] for key in keys): row for row in new[collection]}
                                require(
                                    all(new_rows.get(tuple(row[key] for key in keys)) == row for row in old[collection]),
                                    "historical_rewrite",
                                    "Cumulative source history removed or rewrote a prior record",
                                )
                    else:
                        require(
                            manifest["export_sequence"] == 1 and manifest["previous_bundle_sha256"] is None,
                            "delivery_gap",
                            "First delivery must be sequence 1 with no predecessor",
                        )
                    # SQL aggregates a typed latest-invoice relation independently of the raw-event Python rater.
                    con.execute(
                        "CREATE TEMP TABLE bridge_balances (invoice_id VARCHAR, customer_id VARCHAR, invoice_cents BIGINT, "
                        "paid_cents BIGINT, refunded_cents BIGINT, amount_microusd BIGINT, residual_microusd BIGINT)"
                    )
                    for row in latest.values():
                        con.execute(
                            "INSERT INTO bridge_balances VALUES (?,?,?,?,?,?,?)",
                            [
                                row["invoice_id"],
                                row["customer_id"],
                                row["invoice_cents"],
                                amounts[row["invoice_id"]]["paid_cents"],
                                amounts[row["invoice_id"]]["refunded_cents"],
                                row["amount_microusd"],
                                row["rounding_residual_microusd"],
                            ],
                        )
                    cursor = con.execute(
                        "SELECT *, invoice_cents-paid_cents+refunded_cents AS net_balance_cents, "
                        "greatest(invoice_cents-paid_cents+refunded_cents,0) AS gross_unpaid_cents, "
                        "greatest(paid_cents-refunded_cents-invoice_cents,0) AS credit_cents "
                        "FROM bridge_balances ORDER BY customer_id,invoice_id"
                    )
                    balances = [dict(zip([column[0] for column in cursor.description], row, strict=True)) for row in cursor.fetchall()]
                    totals = {
                        key: sum(row[key] for row in balances) for key in balances[0] if key.endswith("_cents") or key.endswith("_microusd")
                    }
                    require(
                        totals["amount_microusd"] == totals["invoice_cents"] * 10000 + totals["residual_microusd"],
                        "rounding",
                        "Rounding residual does not reconcile",
                    )
                    report = {
                        "schema_version": 1,
                        "scope": "Synthetic USD estimates and simulated collections; not issued invoices or revenue recognition",
                        "bundle_sha256": expected_hash,
                        "sequence": manifest["export_sequence"],
                        "currency": "USD",
                        "billing_cutoff_utc": manifest["source_cutoff_utc"],
                        "account_cutoffs": {row["account_id"]: row["source_cutoff_utc"] for row in payload["source_snapshots.json"]},
                        "cash_cutoff_utc": cash["knowledge_cutoff_utc"],
                        "cash_sha256": cash_hash,
                        "invoice_history": rated,
                        "revision_changes": revision_changes(payload["statements.json"]),
                        "balances": balances,
                        "totals": totals,
                        "statement_source_links": [
                            {"statement_id": row["id"], "source_sha256": row["source_sha256"], "lines": row["lines"]}
                            for row in payload["statements.json"]
                        ],
                    }
                    report["change_from_previous"] = {key: totals[key] - prior["totals"][key] for key in totals} if prior else None
                    con.execute(
                        "INSERT INTO meterwork_usd.deliveries VALUES (?,?,?,?,?,?,?)",
                        [
                            expected_hash,
                            manifest["stream_id"],
                            manifest["export_sequence"],
                            cash_hash,
                            canonical(payload),
                            canonical(report),
                            sha(report),
                        ],
                    )
                    if fail_after_insert:
                        raise Rejected("injected_failure", "Controlled failure after validated delivery insert and before promotion")
                    if _after_insert is not None:
                        _after_insert(con)
                    con.execute("DELETE FROM meterwork_usd.active")
                    con.execute("INSERT INTO meterwork_usd.active VALUES (1,?)", [expected_hash])
                    if _after_promote is not None:
                        _after_promote(con)
                    con.execute("COMMIT")
                    return {"status": "accepted", "bundle_sha256": expected_hash, "report_sha256": sha(report), "totals": totals}
                except BaseException:
                    con.execute("ROLLBACK")
                    raise
        except (KeyError, TypeError, IndexError):
            return self._reject(expected_hash, Rejected("shape", "Delivery has missing or invalid required fields"), prior)
        except Rejected as error:
            return self._reject(expected_hash, error, prior)

    def _reject(self, expected_hash, error, prior):
        require(self.current() == prior, "unsafe_failure", "Rejected delivery changed the prior report")
        preserved = sha(prior) if prior else None
        with duckdb.connect(self.path) as con:
            con.execute(
                "INSERT INTO meterwork_usd.exceptions VALUES (?,?,?,?,?)",
                [datetime.now(timezone.utc), expected_hash, error.code, str(error), preserved],
            )
        return {"status": "rejected", "code": error.code, "detail": str(error), "preserved_report_sha256": preserved}


def render_report(report):
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(row[key]))}</td>"
            for key in (
                "customer_id",
                "invoice_cents",
                "paid_cents",
                "refunded_cents",
                "gross_unpaid_cents",
                "credit_cents",
                "residual_microusd",
            )
        )
        + "</tr>"
        for row in report["balances"]
    )
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Meterwork → Finance reconciliation</title><style>body{max-width:1050px;margin:48px auto;padding:0 24px;background:#17201e;"
        "color:#eef5f2;font:17px/1.6 system-ui}a{color:#94e2c3}h1{line-height:1.2}table{border-collapse:collapse;width:100%;font-size:14px}"
        "td,th{padding:10px;border-bottom:1px solid #41504a;text-align:right}td:first-child,th:first-child{text-align:left}"
        "code{overflow-wrap:anywhere}"
        ".scope{color:#b8c8c0}.card{padding:18px;background:#22312b;border-radius:8px;margin:20px 0}</style>"
        "<h1>Trace usage into reconciled financial reporting</h1>"
        "<p class='scope'>Synthetic USD billing estimates and simulated collections. "
        "Separate from Finance Mart's GBP historical data. No payment processing or real recovered revenue.</p>"
        "<p>Meterwork API → immutable source bundle → independent rating → rounded invoice projection → SQL balances.</p>"
        f"<div class='card'>Accepted delivery {report['sequence']} · <code>{report['bundle_sha256']}</code><br>"
        "All monetary columns below are integer USD cents, except the signed microUSD rounding residual.</div>"
        "<table><thead><tr><th>Customer</th><th>Invoice</th><th>Paid</th><th>Refunded</th><th>Gross unpaid</th><th>Credit</th>"
        f"<th>Residual microUSD</th></tr></thead><tbody>{rows}</tbody></table>"
        "<p>Credits are shown separately from unpaid exposure. Each invoice is rounded once from its complete statement; "
        "residuals are retained.</p>"
        "<p><a href='report.json'>Inspect every revision and source-event link</a> · "
        "<a href='evidence.json'>Inspect failure/replay evidence</a> · "
        "<a href='../../METERWORK_BRIDGE.md'>Read the contract and limitations</a></p></html>"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--cash", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    result = Bridge(args.database).receive(args.bundle, json.loads(args.cash.read_bytes()), args.expected_sha256)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"accepted", "replayed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
