"""Batch inference from a trusted, checksummed customer-value bundle.

Only load bundles you created or trust: joblib is a Python serialization format,
not a safe format for arbitrary third-party model downloads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import pandas as pd
import sklearn

from mart.customer_value import predict, snapshot, timestamp
from mart.pipeline import build
from mart.value_cli import digest, publish, write_json


def score(bundle: Path, raw: Path, cutoff: str, coverage_end: str, output: Path) -> dict:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
    for name in ["model.joblib", "report.json"]:
        if digest(bundle / name) != manifest["artifacts"][name]["sha256"]:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    if manifest["runtime"]["scikit_learn"] != sklearn.__version__:
        raise ValueError("Use the scikit-learn version recorded in the model bundle")
    available_after = timestamp(report["splits"]["validation"]) + pd.Timedelta(days=report["contract"]["horizon_days"])
    if timestamp(cutoff) < available_after:
        raise ValueError("Model selection outcomes were not available at this prediction cutoff")
    models = joblib.load(bundle / "model.joblib")
    con = build(raw)
    try:
        frame = snapshot(con, cutoff, coverage_end, labelled=False)
        scores = predict(models, frame)
        metadata = {
            "cutoff": cutoff,
            "coverage_end_exclusive": coverage_end,
            "customers": len(scores),
            "outcomes_available": False,
            "source_csv_sha256": digest(raw),
            "model_sha256": manifest["artifacts"]["model.joblib"]["sha256"],
            "selection_available_from": str(available_after.date()),
            "scope": "Batch scoring only. No customer contact, retention effect or future outcome observed.",
        }

        def generate(stage):
            scores.to_csv(stage / "scores.csv", index=False)
            metadata["scores_sha256"] = digest(stage / "scores.csv")
            write_json(stage / "metadata.json", metadata)
            return metadata

        return publish(output, generate)
    finally:
        con.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--coverage-end", required=True, help="Known complete source coverage end, exclusive")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(score(args.bundle, args.raw, args.cutoff, args.coverage_end, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
