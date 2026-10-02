"""Daily ELT: contracts -> extract/load -> GX raw -> dbt -> GX marts -> tiering -> publish.

Pipeline tasks run in an isolated interpreter (``PIPELINE_PYTHON``) because the
data stack (pandas 2.2+, SQLAlchemy 2, Great Expectations) conflicts with
Airflow 2's pinned dependencies. dbt runs from its own virtualenv (``DBT_BIN``).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from airflow.datasets import Dataset
from airflow.decorators import dag, task, task_group
from airflow.operators.bash import BashOperator

from sales_pipeline.monitoring.alerting import airflow_failure_callback, airflow_sla_miss_callback

PIPELINE_PYTHON = os.getenv("PIPELINE_PYTHON", sys.executable)
DBT_DIR = os.getenv("DBT_PROJECT_DIR", "/opt/pipeline/dbt")
DBT_BIN = os.getenv("DBT_BIN", "dbt")
DBT_FLAGS = f"--project-dir {DBT_DIR} --profiles-dir {DBT_DIR} --target {os.getenv('DBT_TARGET', 'postgres')}"
SOURCES = ["customers", "products", "orders", "order_items"]
RUN_ID = "{{ run_id }}"

RAW_DATASETS = [Dataset(f"warehouse://raw/{s}") for s in SOURCES]
MARTS_DATASET = Dataset("warehouse://marts/core")
TIERS_DATASET = Dataset("warehouse://marts/mart_customer_tiers")

default_args = {
    "owner": "data-engineering",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "on_failure_callback": airflow_failure_callback,
    "sla": timedelta(hours=2),
}

pipeline_task = task.external_python(python=PIPELINE_PYTHON, expect_airflow=False)


def dbt(task_id: str, command: str, **kwargs) -> BashOperator:
    return BashOperator(task_id=task_id, bash_command=f"{DBT_BIN} {command} {DBT_FLAGS}",
                        append_env=True, **kwargs)


@dag(
    dag_id="sales_elt_pipeline",
    description="Sales ELT with dbt, Great Expectations, tiering and Tableau publishing",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    sla_miss_callback=airflow_sla_miss_callback,
    tags=["elt", "dbt", "great_expectations", "tiering"],
    doc_md=__doc__,
)
def sales_elt_pipeline():
    @pipeline_task
    def check_source_contracts(sources: list[str], run_id: str) -> list[dict]:
        """Fail fast on schema drift in any landed file before loading anything."""
        from sales_pipeline.ingestion.extract import extract
        from sales_pipeline.monitoring.alerting import send_alerts
        from sales_pipeline.monitoring.results import persist_results
        from sales_pipeline.monitoring.schema_drift import check_dataframe

        checks = [check_dataframe(s, extract(s).frame).to_check() for s in sources]
        for c in checks:
            c.run_id = run_id
        persist_results(checks)
        send_alerts(checks)
        failed = [c for c in checks if c.failed]
        if failed:
            raise RuntimeError("Schema drift: " + "; ".join(f"{c.target}: {c.message}" for c in failed))
        return [c.to_dict() for c in checks]

    @task_group(group_id="extract_load")
    def extract_load():
        @task.external_python(python=PIPELINE_PYTHON, expect_airflow=False, outlets=RAW_DATASETS)
        def load(source: str, run_id: str) -> dict:
            from sales_pipeline.ingestion.load import load_source
            from sales_pipeline.monitoring.alerting import send_alerts
            from sales_pipeline.monitoring.results import CheckResult

            result = load_source(source, batch_id=run_id[-24:], run_id=run_id)
            send_alerts([CheckResult.from_dict(c) for c in result["checks"]])
            return {k: v for k, v in result.items() if k != "checks"}

        return load.partial(run_id=RUN_ID).expand(source=SOURCES)

    @pipeline_task
    def validate(layer: str, run_id: str) -> list[str]:
        from sales_pipeline.quality.validate import run_layer

        return [f"{c.severity}: {c.check_name}" for c in run_layer(layer, run_id=run_id)]

    @pipeline_task
    def run_tiering(run_id: str) -> dict:
        from sales_pipeline.tiering.pipeline import run

        return run(run_id=run_id)

    @pipeline_task
    def publish_tableau() -> dict:
        from sales_pipeline.publish.tableau import publish

        return publish()

    @task.external_python(python=PIPELINE_PYTHON, expect_airflow=False, trigger_rule="all_done")
    def post_run_monitoring(run_id: str) -> dict:
        from sales_pipeline.monitoring.runner import run_all_checks

        results = run_all_checks(run_id=run_id)
        return {s: sum(r.severity == s for r in results) for s in ("pass", "warn", "error")}

    dbt_deps = dbt("dbt_deps", "deps")
    dbt_freshness = dbt("dbt_source_freshness", "source freshness --select source:raw")
    dbt_build = dbt("dbt_build_core", "build --exclude tag:post_tiering", outlets=[MARTS_DATASET])
    dbt_post = dbt("dbt_build_post_tiering", "build --select tag:post_tiering", outlets=[TIERS_DATASET])
    dbt_docs = dbt("dbt_docs_generate", "docs generate", trigger_rule="all_done")

    contracts = check_source_contracts(SOURCES, RUN_ID)
    loaded = extract_load()
    raw_ok = validate.override(task_id="validate_raw")("raw", RUN_ID)
    marts_ok = validate.override(task_id="validate_marts")("marts", RUN_ID)
    tiers = run_tiering(RUN_ID)
    tiers_ok = validate.override(task_id="validate_tiering")("tiering", RUN_ID)
    published = publish_tableau()

    contracts >> loaded >> raw_ok >> dbt_deps >> dbt_freshness >> dbt_build >> marts_ok
    marts_ok >> tiers >> tiers_ok >> dbt_post >> [published, dbt_docs]
    [published, dbt_docs] >> post_run_monitoring(RUN_ID)


sales_elt_pipeline()
