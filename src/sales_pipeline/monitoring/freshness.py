"""Freshness monitoring: business-data staleness and pipeline load staleness."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from sales_pipeline import warehouse
from sales_pipeline.config import pipeline_config
from sales_pipeline.monitoring.results import CheckResult


def _to_utc(ts) -> datetime | None:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    ts = pd.Timestamp(ts)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").to_pydatetime()


def evaluate_freshness(
    target: str,
    latest,
    warn_after_hours: float,
    error_after_hours: float,
    now: datetime | None = None,
    check_name: str = "data_freshness",
) -> CheckResult:
    now = now or datetime.now(timezone.utc)
    latest_utc = _to_utc(latest)
    if latest_utc is None:
        return CheckResult("freshness", target, "error", "no records found", check_name=check_name,
                           threshold=error_after_hours)
    age_h = (now - latest_utc).total_seconds() / 3600
    if age_h >= error_after_hours:
        severity = "error"
    elif age_h >= warn_after_hours:
        severity = "warn"
    else:
        severity = "pass"
    return CheckResult(
        "freshness", target, severity,
        f"latest record {age_h:.1f}h old (warn {warn_after_hours}h / error {error_after_hours}h)",
        check_name=check_name,
        observed_value=round(age_h, 2),
        threshold=float(error_after_hours if severity == "error" else warn_after_hours),
        details={"latest": latest_utc.isoformat()},
    )


def check_freshness(engine=None, now: datetime | None = None) -> list[CheckResult]:
    engine = engine or warehouse.get_engine()
    cfg = pipeline_config()
    schema = cfg["raw_schema"]
    results: list[CheckResult] = []
    for source in cfg["sources"]:
        target = f"{schema}.{source}"
        if not warehouse.table_exists(engine, schema, source):
            results.append(CheckResult("freshness", target, "error", "table does not exist",
                                       check_name="load_freshness"))
            continue
        load_cfg = cfg["freshness"]["load"]
        latest_load = warehouse.scalar(engine, f"SELECT MAX(_loaded_at) FROM {target}")
        results.append(evaluate_freshness(target, latest_load, load_cfg["warn_after_hours"],
                                          load_cfg["error_after_hours"], now, "load_freshness"))
        data_cfg = cfg["freshness"].get(source)
        if data_cfg:
            latest = warehouse.scalar(engine, f"SELECT MAX({data_cfg['column']}) FROM {target}")
            results.append(evaluate_freshness(target, latest, data_cfg["warn_after_hours"],
                                              data_cfg["error_after_hours"], now, "data_freshness"))
    return results
