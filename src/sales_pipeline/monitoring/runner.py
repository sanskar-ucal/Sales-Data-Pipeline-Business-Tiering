"""Run every warehouse monitoring check, persist results and alert."""

from __future__ import annotations

from sales_pipeline import warehouse
from sales_pipeline.config import pipeline_config
from sales_pipeline.monitoring.alerting import send_alerts
from sales_pipeline.monitoring.freshness import check_freshness
from sales_pipeline.monitoring.results import CheckResult, persist_results
from sales_pipeline.monitoring.schema_drift import check_warehouse_table
from sales_pipeline.monitoring.volume import check_table_volumes


def run_all_checks(engine=None, run_id: str | None = None, alert: bool = True) -> list[CheckResult]:
    engine = engine or warehouse.get_engine()
    results: list[CheckResult] = []
    results += check_freshness(engine)
    results += check_table_volumes(engine)
    for source in pipeline_config()["sources"]:
        results.append(check_warehouse_table(engine, source).to_check())
    for r in results:
        r.run_id = run_id
    persist_results(results, engine)
    if alert:
        send_alerts(results)
    return results
