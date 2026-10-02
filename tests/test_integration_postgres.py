"""Load -> monitor round trip against a real Postgres (skipped if unreachable)."""

import pytest
import sqlalchemy as sa

from sales_pipeline import warehouse
from sales_pipeline.data_generator import generate, write


@pytest.fixture(scope="module")
def engine():
    eng = warehouse.get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(sa.text("select 1"))
    except Exception:
        pytest.skip("Postgres not reachable (set PG_* env vars)")
    return eng


def test_incremental_load_and_monitoring(engine, tmp_path, monkeypatch):
    import sales_pipeline.config as config

    schemas = {"raw_schema": "it_raw", "monitoring_schema": "it_monitoring", "tiering_schema": "it_tiering"}
    base = config.pipeline_config()
    monkeypatch.setattr("sales_pipeline.ingestion.load.pipeline_config", lambda: {**base, **schemas})
    monkeypatch.setattr("sales_pipeline.monitoring.volume.pipeline_config", lambda: {**base, **schemas})
    monkeypatch.setattr("sales_pipeline.monitoring.results.pipeline_config", lambda: {**base, **schemas})
    for s in schemas.values():
        warehouse.execute(engine, f"DROP SCHEMA IF EXISTS {s} CASCADE")

    from sales_pipeline.ingestion.load import load_source

    write(generate(n_customers=50, n_products=10, seed=3), tmp_path)
    first = load_source("orders", engine, data_dir=tmp_path)
    second = load_source("orders", engine, data_dir=tmp_path)
    assert first["rows_loaded"] > 0
    assert second["rows_loaded"] == 0  # watermark prevents reloading
    zero_check = [c for c in second["checks"] if c["check_type"] == "volume"][0]
    assert zero_check["severity"] == "error"
    assert warehouse.scalar(engine, "select count(*) from it_monitoring.load_audit") == 2
    for s in schemas.values():
        warehouse.execute(engine, f"DROP SCHEMA IF EXISTS {s} CASCADE")
