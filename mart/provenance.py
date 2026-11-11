"""Fingerprint the exact private input copy used by an artifact build."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def freeze_source(raw: Path, directory: Path) -> tuple[Path, dict]:
    """All consumers read this immutable-by-convention copy, never the live path.

    A producer should atomically replace its input file. This records the copied
    bytes even if a producer changes the original while downstream outputs build;
    it does not claim a transaction with an external file producer.
    """
    directory.mkdir(parents=True, exist_ok=False)
    frozen = directory / raw.name
    shutil.copyfile(raw, frozen)
    return frozen, {"file": raw.name, "bytes": frozen.stat().st_size, "sha256": sha256(frozen),
                    "identity": "Private frozen copy consumed by every output in this build"}


def code_records(root: Path) -> dict[str, str]:
    # Git can change CRLF/LF on checkout without changing source semantics.
    # Explicitly labelled canonical hashes are portable across those checkouts;
    # raw inputs and generated artifacts always retain exact-byte hashes.
    paths = sorted([*root.glob("mart/*.py"), *root.glob("sql/*.sql"), *root.glob("seeds/*.csv")])
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for path in paths}
