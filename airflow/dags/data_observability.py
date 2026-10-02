"""Hourly observability: freshness, volume and schema drift on the warehouse, with alerting."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.operators.bash import BashOperator

from sales_pipeline.monitoring.alerting import airflow_failure_callback

PIPELINE_PYTHON = os.getenv("PIPELINE_PYTHON", sys.executable)
DBT_DIR = os.getenv("DBT_PROJECT_DIR", "/opt/pipeline/dbt")
DBT_BIN = os.getenv("DBT_BIN", "dbt")


@dag(
    dag_id="data_observability",
    description="Hourly freshness, volume and schema-drift monitoring",
    schedule="15 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "data-engineering",
        "retries": 1,
        "retry_delay": timedelta(minutes=2),
        "on_failure_callback": airflow_failure_callback,
    },
    tags=["monitoring", "observability"],
    doc_md=__doc__,
)
def data_observability():
    @task.external_python(python=PIPELINE_PYTHON, expect_airflow=False)
    def warehouse_checks(run_id: str) -> dict:
        """Run all checks, persist + alert, then fail the task if anything is at error severity."""
        from sales_pipeline.monitoring.runner import run_all_checks

        results = run_all_checks(run_id=run_id)
        errors = [r for r in results if r.failed]
        if errors:
            raise RuntimeError(f"{len(errors)} monitoring check(s) at error severity: "
                               + "; ".join(f"{r.check_type}:{r.target}" for r in errors))
        return {s: sum(r.severity == s for r in results) for s in ("pass", "warn", "error")}

    dbt_freshness = BashOperator(
        task_id="dbt_source_freshness",
        bash_command=f"{DBT_BIN} source freshness --project-dir {DBT_DIR} --profiles-dir {DBT_DIR}",
        append_env=True,
        trigger_rule="all_done",
    )

    warehouse_checks("{{ run_id }}") >> dbt_freshness


data_observability()
