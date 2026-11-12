"""Show curated source-to-consumer impact before changing an operational field."""

import argparse
import json
from pathlib import Path

CONTRACT = Path(__file__).resolve().parents[1] / "operational/contract.json"


def impact(field):
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    graph = contract["dependencies"]
    if field not in {f"orders.{name}" for name in contract["fields"]}:
        raise ValueError("Choose a declared orders source field")
    found, pending = set(), [field]
    while pending:
        for child in graph.get(pending.pop(), []):
            if child not in found:
                found.add(child)
                pending.append(child)
    return {"field": field, "contract_version": contract["version"], "lineage_kind": contract["lineage_kind"],
            "affected": sorted(found), "historical_uci_outputs_affected": False,
            "required_checks": ["source contract validation", "source-to-current-order reconciliation",
                                "daily amount reconciliation", "duplicate replay and complete rebuild", "recovery exercise"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("field")
    print(json.dumps(impact(parser.parse_args().field), indent=2))
