"""Reproduce a staged customer-value evidence bundle: python -m mart.value_cli."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from mart.customer_value import (
    COVERAGE_END,
    COVERAGE_START,
    FEATURES,
    TEST,
    TRAIN,
    VALIDATION,
    assess,
    predict,
    snapshot,
    train_select,
    validate_splits,
)
from mart.ingest import URL, fetch
from mart.pipeline import ROOT, build


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(part)
    return sha.hexdigest()


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def publish(output: Path, generate) -> dict:
    """Publish one new version, never mutate or destroy an older complete bundle."""
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Use a new output directory to preserve the previous evidence bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".customer-value-", dir=output.parent))
    try:
        summary = generate(stage)
        if output.exists():
            raise FileExistsError(output)
        os.replace(stage, output)
        return summary
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def report_page(report: dict) -> str:
    rows = "".join(
        f"<tr><td>{r['cutoff']}</td><td>{r['customers']:,}</td><td>{r['roc_auc']:.3f}</td>"
        f"<td>{r['brier']:.3f}</td><td>£{r['revenue_mae_gbp']:,.0f}</td>"
        f"<td>£{r['past_90d_baseline_mae_gbp']:,.0f}</td>"
        f"<td>{r['top_20_percent_future_revenue_share']:.1%}</td></tr>"
        for r in report["test_windows"]
    )
    selected = report["validation_selection"]
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Customer future value | Vasilen Kanchev</title><style>
:root{color-scheme:dark}body{margin:0;background:#141b20;color:#e9f0f2;font:17px/1.65 system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:55px 24px}a{color:#7fe0c0}h1{font-size:clamp(32px,5vw,56px);line-height:1.15;max-width:800px}
.eyebrow{color:#7fe0c0;letter-spacing:.1em;text-transform:uppercase;font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:20px}
.card{border:1px solid #36434c;border-radius:12px;padding:22px;background:#1c252c}.muted{color:#b1c1cb}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:15px 12px;border-bottom:1px solid #36434c}th{color:#7fe0c0}h2{margin-top:42px}code{font-size:14px}li{margin:8px 0}
</style><main><p class="eyebrow">Finance Mart / Applied data science</p><h1>Who will buy again, and how much?</h1>
<p class="muted">A reproducible 90-day customer prediction study on public UK retail invoices. Models are selected on a past validation window and frozen before two future-period tests.</p>
<div class="grid"><div class="card"><strong>1,067,371 source lines</strong><br>UCI Online Retail II, 2009–2011. No employer or live customer data.</div>
<div class="card"><strong>180 days of observed behaviour</strong><br>Recency, order frequency, product variety, gross purchase value and returns.</div>
<div class="card"><strong>90-day future outcomes</strong><br>Repeat purchase and gross product spend. Not subscription churn, profit or lifetime value.</div></div>
<h2>Untouched future-window results</h2><p>"""
        + html.escape(
            f"Selected on January 2011 only: {selected['selected_classifier']} for repeat-purchase probability; "
            f"{selected['selected_revenue']} for gross spend. Lower Brier score/MAE is better."
        )
        + """</p><div class="scroll"><table><thead><tr><th>Prediction date</th><th>Customers</th><th>ROC-AUC</th><th>Brier</th><th>Spend MAE</th><th>Past-spend baseline MAE</th><th>Future revenue captured by top 20%</th></tr></thead><tbody>"""
        + rows
        + """</tbody></table></div>
<p class="muted">Revenue capture is retrospective ranking evaluation. It is not extra revenue created by an offer or proof that intervention works.</p>
<h2>The decision this supports</h2><p>A team with limited review capacity can inspect the highest predicted-value customers first. The CSV includes a deterministic top-20% flag. Choosing a retention offer requires treatment-effect evidence and actual costs; neither is available here.</p>
<h2>What keeps the evaluation honest</h2><ul><li>Features stop strictly before the prediction date. Only mature future labels are used in training and model selection.</li>
<li>Training windows: July and October 2010. Validation: January 2011. Tests: April and July 2011. Each outcome window is 90 days and disjoint; every snapshot has at least 180 days of source coverage.</li>
<li>Customers may recur across time windows: this evaluates future behaviour of an existing customer base, not generalization to unseen customers.</li>
<li>The December 9, 2011 shadow score has no observed future outcome. It excludes the partial final source day and demonstrates batch inference only.</li>
<li>Gross purchase spend does not subtract future returns, shipping, fees or marketing costs. No revenue uplift, causal effect or realized savings is claimed.</li></ul>
<h2>Inspect and reproduce</h2><p><a href="report.json">Metrics, calibration and limitations</a> · <a href="test_predictions.csv">All held-out predictions</a> · <a href="shadow_scores.csv">Historical shadow scores</a> · <a href="manifest.json">Source, code and artifact fingerprints</a></p>
<p><a href="https://github.com/VasilenData/ecommerce-finance-mart">Source and reproducible commands</a> · <a href="../explorer/">Finance explorer</a></p></main></html>"""
    )


def run(raw: Path, output: Path) -> dict:
    validate_splits(TRAIN, VALIDATION, TEST, COVERAGE_END)
    con = build(raw)
    try:
        raw_count = con.execute("select count(*) from stg_lines").fetchone()[0]
        if raw_count != 1_067_371:
            raise ValueError("This recorded protocol requires both complete UCI sheets (1,067,371 rows)")
        train = pd.concat([snapshot(con, x, COVERAGE_END) for x in TRAIN], ignore_index=True)
        validation = snapshot(con, VALIDATION, COVERAGE_END)
        models, selection = train_select(train, validation)
        report = {
            "schema_version": 1,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": {
                "url": URL,
                "raw_rows": raw_count,
                "csv_sha256": digest(raw),
                "coverage_start_inclusive": COVERAGE_START,
                "coverage_end_exclusive": COVERAGE_END,
            },
            "contract": {
                "observation_days": 180,
                "horizon_days": 90,
                "features": FEATURES,
                "eligibility": "Registered customer with at least one positive product purchase in previous 180 days",
                "target": "Any positive product purchase and total gross product spend in next 90 days, GBP",
                "timezone": "Source naive local wall time; no UTC conversion",
                "window_boundaries": "[start, end)",
            },
            "splits": {
                "train": TRAIN,
                "validation": VALIDATION,
                "test": TEST,
                "train_customer_snapshots": len(train),
                "validation_customers": len(validation),
            },
            "validation_selection": selection,
            "test_windows": [],
            "limits": [
                "Historical public data from one retailer; no production performance or retention impact measured.",
                "Existing customers recur across disjoint time windows; this is not an unseen-customer test.",
                "Observed age begins at the first sale in this source, not true customer acquisition.",
                "Sales reversed later are still gross purchases; future returns and margins are not target inputs.",
                "A small fixed model comparison; future test results must not be reused to tune this protocol.",
                "Two future windows do not establish all-season robustness; calibration can shift.",
                "Source coverage is the declared complete UCI export, not a claim to detect absent upstream transactions.",
            ],
        }
        scored_tests = []
        for cutoff in TEST:
            frame = snapshot(con, cutoff, COVERAGE_END)
            scores = predict(models, frame)
            report["test_windows"].append(assess(frame, scores))
            scores["actual_repeat_90d"] = frame["repeat_90d"].to_numpy()
            scores["actual_gross_90d_gbp"] = frame["future_gross_90d"].to_numpy()
            scores["past_90d_baseline_gbp"] = frame["gross_90d"].to_numpy()
            scored_tests.append(scores)
        shadow = snapshot(con, COVERAGE_END, COVERAGE_END, labelled=False)
        shadow_scores = predict(models, shadow)
        drift = {}
        for feature in FEATURES:
            low, high = np.quantile(train[feature], [0.01, 0.99])
            drift[feature] = {
                "train_p01": float(low),
                "train_p99": float(high),
                "shadow_outside_training_range_fraction": float(((shadow[feature] < low) | (shadow[feature] > high)).mean()),
            }
        report["shadow"] = {
            "cutoff": COVERAGE_END,
            "customers": len(shadow),
            "outcomes_available": False,
            "description": "Historical batch-scoring demonstration; no offers are sent",
            "feature_range_diagnostics": drift,
        }

        def generate(stage):
            write_json(stage / "report.json", report)
            pd.concat(scored_tests, ignore_index=True).to_csv(stage / "test_predictions.csv", index=False)
            shadow_scores.to_csv(stage / "shadow_scores.csv", index=False)
            joblib.dump(models, stage / "model.joblib", compress=3)
            (stage / "index.html").write_text(report_page(report), encoding="utf-8")
            code_files = sorted([*ROOT.glob("mart/*.py"), *ROOT.glob("sql/*.sql"), *ROOT.glob("seeds/*.csv")])
            try:
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            except (OSError, subprocess.CalledProcessError):
                revision = None
            manifest = {
                "schema_version": 1,
                "code_revision": revision,
                "code_files_sha256": {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p) for p in code_files},
                "source_csv_sha256": report["source"]["csv_sha256"],
                "runtime": {
                    "python": platform.python_version(),
                    "numpy": np.__version__,
                    "pandas": pd.__version__,
                    "scikit_learn": sklearn.__version__,
                },
                "model_usage": "Local trusted artifact only. Never deserialize a model.joblib from an untrusted source.",
                "artifacts": {p.name: {"sha256": digest(p), "bytes": p.stat().st_size} for p in sorted(stage.iterdir())},
            }
            write_json(stage / "manifest.json", manifest)
            return report

        return publish(output, generate)
    finally:
        con.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw_online_retail_II.csv")
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing versions are never overwritten")
    parser.add_argument("--download", action="store_true", help="Fetch the public UCI source if the default CSV is absent")
    args = parser.parse_args(argv)
    if args.download:
        if args.raw != ROOT / "data" / "raw_online_retail_II.csv":
            parser.error("--download uses the default UCI source path")
        fetch(ROOT / "data")
    report = run(args.raw, args.output)
    print(
        json.dumps(
            {"output": str(args.output), "selection": report["validation_selection"], "test_windows": report["test_windows"]},
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
