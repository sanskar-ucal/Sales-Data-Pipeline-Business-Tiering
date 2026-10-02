"""Volume anomaly detection against trailing load history (monitoring.load_audit)."""

from __future__ import annotations

import numpy as np

from sales_pipeline import warehouse
from sales_pipeline.config import pipeline_config
from sales_pipeline.monitoring.results import CheckResult


def evaluate_volume(target: str, current: int, history: list[int], cfg: dict | None = None,
                    allow_zero: bool = False) -> CheckResult:
    """Score ``current`` row count against prior batches using z-score and % change."""
    cfg = cfg or pipeline_config()["volume"]
    hist = np.asarray(history[-cfg["history_window"]:], dtype=float)
    details = {"current": int(current), "history": [int(h) for h in hist]}

    if current == 0 and not allow_zero:
        return CheckResult("volume", target, cfg["zero_rows_severity"], "batch contained zero rows",
                           check_name="row_count_anomaly", observed_value=0.0, details=details)
    if len(hist) < cfg["min_history"]:
        return CheckResult("volume", target, "pass",
                           f"{current} rows; insufficient history ({len(hist)}/{cfg['min_history']}) for anomaly test",
                           check_name="row_count_anomaly", observed_value=float(current), details=details)

    mean = float(hist.mean())
    std = float(hist.std(ddof=1)) if len(hist) > 1 else 0.0
    z = (current - mean) / std if std > 0 else (0.0 if current == mean else np.inf)
    pct = (current - mean) / mean if mean > 0 else (0.0 if current == 0 else np.inf)
    details.update({"mean": round(mean, 2), "std": round(std, 2), "z_score": round(float(z), 3),
                    "pct_change": round(float(pct), 3)})

    # Both the z-score and the relative change must breach: avoids alerting on
    # tiny absolute moves in very stable series and on noise in volatile ones.
    if abs(z) >= cfg["z_score_error"] and abs(pct) >= cfg["pct_change_error"]:
        severity = "error"
    elif abs(z) >= cfg["z_score_warn"] and abs(pct) >= cfg["pct_change_warn"]:
        severity = "warn"
    else:
        severity = "pass"
    return CheckResult(
        "volume", target, severity,
        f"{current} rows vs trailing mean {mean:.0f} (z={z:.2f}, change={pct:+.0%})",
        check_name="row_count_anomaly", observed_value=float(current),
        threshold=float(cfg["z_score_error"] if severity == "error" else cfg["z_score_warn"]),
        details=details,
    )


def load_history(engine, source: str, metric: str = "rows_loaded") -> list[int]:
    schema = pipeline_config()["monitoring_schema"]
    if not warehouse.table_exists(engine, schema, "load_audit"):
        return []
    df = warehouse.read_sql(
        engine,
        f"SELECT {metric} FROM {schema}.load_audit WHERE source = :source AND status = 'success' "
        f"ORDER BY finished_at",
        {"source": source},
    )
    return df[metric].astype(int).tolist()


def check_batch_volume(engine, source: str, rows_loaded: int, table_rows: int, strategy: str) -> CheckResult:
    """Incremental sources are judged on batch size, full refreshes on table size."""
    target = f"{pipeline_config()['raw_schema']}.{source}"
    if strategy == "incremental":
        return evaluate_volume(target, rows_loaded, load_history(engine, source, "rows_loaded"))
    return evaluate_volume(target, table_rows, load_history(engine, source, "table_rows_after"))


def check_table_volumes(engine=None) -> list[CheckResult]:
    """Current table sizes vs the sizes recorded after previous loads."""
    engine = engine or warehouse.get_engine()
    cfg = pipeline_config()
    results = []
    for source in cfg["sources"]:
        target = f"{cfg['raw_schema']}.{source}"
        if not warehouse.table_exists(engine, cfg["raw_schema"], source):
            continue
        current = int(warehouse.scalar(engine, f"SELECT COUNT(*) FROM {target}"))
        history = load_history(engine, source, "table_rows_after")
        result = evaluate_volume(target, current, history[:-1] if history else [])
        result.check_name = "table_row_count"
        if history and current < history[-1]:
            result.severity = "error"
            result.message = f"table shrank from {history[-1]} to {current} rows since last load"
        results.append(result)
    return results
