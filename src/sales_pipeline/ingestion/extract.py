"""Extract source files from the landing zone and conform them to their contract."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from sales_pipeline.config import get_settings, pipeline_config, schema_contracts

log = logging.getLogger(__name__)


@dataclass
class Extracted:
    source: str
    path: Path
    checksum: str
    frame: pd.DataFrame  # raw string values, exactly as landed


def file_checksum(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(source: str, data_dir: Path | None = None) -> Extracted:
    cfg = pipeline_config()["sources"][source]
    path = Path(data_dir or get_settings().data_dir) / cfg["file"]
    if not path.exists():
        raise FileNotFoundError(f"Source file for {source!r} not found at {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    frame.columns = [c.strip().lower() for c in frame.columns]
    log.info("Extracted %d rows from %s", len(frame), path)
    return Extracted(source, path, file_checksum(path), frame)


def conform(source: str, frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Cast columns to contract types and drop columns not in the contract.

    Returns the conformed frame and the list of dropped (uncontracted) columns.
    """
    contract = schema_contracts()[source]
    dropped = [c for c in frame.columns if c not in contract]
    out = pd.DataFrame(index=frame.index)
    for col, spec in contract.items():
        if col not in frame.columns:
            out[col] = pd.NA
            continue
        s = frame[col]
        kind = spec["type"]
        if kind == "integer":
            out[col] = pd.to_numeric(s, errors="raise").astype("Int64")
        elif kind == "float":
            out[col] = pd.to_numeric(s, errors="raise").astype(float)
        elif kind == "boolean":
            out[col] = s.str.lower().map({"true": True, "t": True, "yes": True, "1": True,
                                          "false": False, "f": False, "no": False, "0": False})
        elif kind == "date":
            out[col] = pd.to_datetime(s, errors="raise").dt.date
        elif kind == "timestamp":
            out[col] = pd.to_datetime(s, errors="raise")
        else:
            out[col] = s
    if dropped:
        log.warning("%s: dropping uncontracted columns %s", source, dropped)
    return out, dropped
