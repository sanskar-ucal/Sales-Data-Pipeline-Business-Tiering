from datetime import datetime, timedelta, timezone

import pandas as pd

from sales_pipeline.data_generator import inject_schema_drift
from sales_pipeline.monitoring.freshness import evaluate_freshness
from sales_pipeline.monitoring.schema_drift import check_dataframe, compare, infer_logical_type
from sales_pipeline.monitoring.volume import evaluate_volume

VOLUME_CFG = {"history_window": 14, "min_history": 3, "z_score_warn": 2.5, "z_score_error": 4.0,
              "pct_change_warn": 0.5, "pct_change_error": 0.9, "zero_rows_severity": "error"}


def test_freshness_thresholds():
    now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    assert evaluate_freshness("t", now - timedelta(hours=1), 24, 48, now).severity == "pass"
    assert evaluate_freshness("t", now - timedelta(hours=30), 24, 48, now).severity == "warn"
    assert evaluate_freshness("t", now - timedelta(hours=50), 24, 48, now).severity == "error"
    assert evaluate_freshness("t", None, 24, 48, now).severity == "error"


def test_volume_anomalies():
    history = [1000, 1020, 990, 1010, 1005]
    assert evaluate_volume("t", 1003, history, VOLUME_CFG).severity == "pass"
    assert evaluate_volume("t", 40, history, VOLUME_CFG).severity == "error"
    assert evaluate_volume("t", 0, history, VOLUME_CFG).severity == "error"
    assert evaluate_volume("t", 5000, [1, 2], VOLUME_CFG).severity == "pass"  # insufficient history


def test_infer_logical_types():
    assert infer_logical_type(pd.Series(["1", "2", None])) == "integer"
    assert infer_logical_type(pd.Series(["1.5", "2"])) == "float"
    assert infer_logical_type(pd.Series(["2026-01-01"])) == "date"
    assert infer_logical_type(pd.Series(["2026-01-01 10:00:00"])) == "timestamp"
    assert infer_logical_type(pd.Series(["abc", "1"])) == "string"


def test_drift_detection():
    contract = {"a": {"type": "integer"}, "b": {"type": "string"}}
    report = compare("x", {"a": "string", "c": "integer"}, contract)
    assert report.added == ["c"] and report.removed == ["b"] and "a" in report.type_changes
    assert report.to_check({"on_added_column": "warn", "on_removed_column": "error",
                            "on_type_change": "error"}).severity == "error"


def test_generated_sources_match_contracts(source_tables):
    for name, df in source_tables.items():
        raw = df.astype(str).replace({"<NA>": None, "None": None, "nan": None, "NaT": None})
        assert not check_dataframe(name, raw).has_drift, name
    drifted = inject_schema_drift(source_tables)["orders"].astype(str)
    report = check_dataframe("orders", drifted)
    assert report.added == ["sales_rep_id"] and "payment_terms_days" in report.type_changes
