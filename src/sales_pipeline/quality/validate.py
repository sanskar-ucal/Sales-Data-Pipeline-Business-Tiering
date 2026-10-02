"""Run Great Expectations suites against warehouse relations."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import great_expectations as gx
import pandas as pd

from sales_pipeline import warehouse
from sales_pipeline.config import get_settings
from sales_pipeline.monitoring.results import CheckResult, persist_results
from sales_pipeline.quality.suites import LAYERS

log = logging.getLogger(__name__)


class DataQualityError(RuntimeError):
    pass


def _context():
    ctx = gx.get_context(mode="ephemeral")
    try:
        from great_expectations.data_context.types.base import ProgressBarsConfig

        ctx.variables.progress_bars = ProgressBarsConfig(globally=False)
    except Exception:  # progress bar config moved between GX releases
        pass
    return ctx


def build_suite(name: str, spec: dict[str, Any]) -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name=name)
    for exp_name, kwargs in spec["expectations"]:
        suite.add_expectation(getattr(gx.expectations, exp_name)(**kwargs))
    return suite


def validate_dataframe(df: pd.DataFrame, name: str, spec: dict[str, Any], context=None):
    """Validate an in-memory frame against a suite spec; returns the GX validation result."""
    ctx = context or _context()
    datasource = ctx.data_sources.add_or_update_pandas(f"ds_{name}")
    asset = datasource.add_dataframe_asset(name=name)
    batch_def = asset.add_batch_definition_whole_dataframe(f"{name}_batch")
    suite = ctx.suites.add_or_update(build_suite(name, spec))
    batch = batch_def.get_batch(batch_parameters={"dataframe": df})
    return batch.validate(suite)


def summarize(name: str, spec: dict[str, Any], result) -> CheckResult:
    failed = [
        {
            "expectation": r.expectation_config.type,
            "kwargs": {k: v for k, v in r.expectation_config.kwargs.items() if k != "batch_id"},
            "unexpected_count": (r.result or {}).get("unexpected_count"),
            "observed_value": (r.result or {}).get("observed_value"),
        }
        for r in result.results
        if not r.success
    ]
    stats = result.statistics
    severity = "pass" if result.success else spec["severity"]
    msg = (f"{stats['successful_expectations']}/{stats['evaluated_expectations']} expectations passed"
           + (f"; failed: {', '.join(f['expectation'] for f in failed)}" if failed else ""))
    return CheckResult(
        check_type="data_quality",
        check_name=name,
        target=spec["relation"],
        severity=severity,
        message=msg,
        observed_value=float(stats["success_percent"] or 0.0),
        threshold=100.0,
        details={"failed_expectations": failed},
    )


def run_layer(layer: str, engine=None, run_id: str | None = None, raise_on_error: bool = True,
              results_dir: Path | None = None) -> list[CheckResult]:
    engine = engine or warehouse.get_engine()
    suites = LAYERS[layer]()
    ctx = _context()
    results_dir = results_dir or (get_settings().output_dir / "ge_results")
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    checks: list[CheckResult] = []
    for name, spec in suites.items():
        schema, table = spec["relation"].split(".")
        if not warehouse.table_exists(engine, schema, table):
            checks.append(CheckResult("data_quality", spec["relation"], spec["severity"],
                                      "relation does not exist", check_name=name, run_id=run_id))
            continue
        df = warehouse.read_sql(engine, f"SELECT * FROM {spec['relation']}")
        result = validate_dataframe(df, name, spec, ctx)
        (results_dir / f"{stamp}_{name}.json").write_text(json.dumps(result.to_json_dict(), indent=2, default=str))
        check = summarize(name, spec, result)
        check.run_id = run_id
        checks.append(check)
        log.info("[%s] %s: %s", check.severity.upper(), name, check.message)

    persist_results(checks, engine)
    errors = [c for c in checks if c.failed]
    if errors and raise_on_error:
        raise DataQualityError("; ".join(f"{c.check_name}: {c.message}" for c in errors))
    return checks
