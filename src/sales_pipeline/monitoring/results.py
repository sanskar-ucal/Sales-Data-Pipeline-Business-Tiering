"""Common result type for monitoring and data-quality checks, plus persistence.

Kept free of heavy top-level imports so Airflow's own interpreter can import
the alerting callbacks without the pipeline's data dependencies.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sales_pipeline.config import pipeline_config

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"pass": 0, "info": 0, "warn": 1, "error": 2}


def worst(severities) -> str:
    return max(severities, key=lambda s: SEVERITY_ORDER[s], default="pass")


@dataclass
class CheckResult:
    check_type: str           # freshness | volume | schema_drift | data_quality
    target: str               # e.g. raw.orders
    severity: str             # pass | warn | error
    message: str
    check_name: str = ""
    observed_value: float | None = None
    threshold: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def failed(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checked_at"] = self.checked_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CheckResult:
        d = dict(d)
        if isinstance(d.get("checked_at"), str):
            d["checked_at"] = datetime.fromisoformat(d["checked_at"])
        return cls(**d)


def persist_results(results: list[CheckResult], engine=None) -> None:
    if not results:
        return
    import pandas as pd
    import sqlalchemy as sa

    from sales_pipeline import warehouse

    engine = engine or warehouse.get_engine()
    schema = pipeline_config()["monitoring_schema"]
    df = pd.DataFrame([
        {
            "checked_at": r.checked_at,
            "run_id": r.run_id,
            "check_type": r.check_type,
            "check_name": r.check_name or r.check_type,
            "target": r.target,
            "severity": r.severity,
            "message": r.message,
            "observed_value": r.observed_value,
            "threshold": r.threshold,
            "details": json.dumps(r.details, default=str),
        }
        for r in results
    ])
    warehouse.write_dataframe(
        engine, df, "check_results", schema, mode="append",
        dtype={"checked_at": sa.DateTime(timezone=True), "observed_value": sa.Float, "threshold": sa.Float},
    )
    log.info("Persisted %d check results to %s.check_results", len(results), schema)
