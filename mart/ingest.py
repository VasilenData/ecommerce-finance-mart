"""Download the UCI Online Retail II workbook and flatten both sheets into one CSV."""
from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path

URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"


def fetch(data_dir: str | Path = "data") -> Path:
    import pandas as pd

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / "raw_online_retail_II.csv"
    if out.exists():
        return out
    with urllib.request.urlopen(URL, timeout=300) as r:  # noqa: S310 (fixed https URL)
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xlsx = z.extract("online_retail_II.xlsx", data_dir)
    sheets = pd.read_excel(xlsx, sheet_name=None, dtype=str)  # about a minute
    pd.concat(sheets.values()).to_csv(out, index=False)
    return out
