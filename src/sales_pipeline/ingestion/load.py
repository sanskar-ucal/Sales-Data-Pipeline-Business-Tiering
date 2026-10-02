"""Load conformed source data into the raw schema with audit metadata."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import sqlalchemy as sa

from sales_pipeline import warehouse
from sales_pipeline.config import pipeline_config, schema_contracts
from sales_pipeline.ingestion.extract import conform, extract
from sales_pipeline.monitoring.results import CheckResult, persist_results
from sales_pipeline.monitoring.schema_drift import check_dataframe
from sales_pipeline.monitoring.volume import check_batch_volume
from sales_pipeline.warehouse import LOGICAL_TO_SQLA

log = logging.getLogger(__name__)


class SchemaDriftError(RuntimeError):
    pass


def _dtype_map(source: str) -> dict[str, Any]:
    dtype = {col: LOGICAL_TO_SQLA[spec["type"]] for col, spec in schema_contracts()[source].items()}
    dtype.update({"_batch_id": sa.Text, "_loaded_at": sa.DateTime(timezone=True), "_source_file": sa.Text})
    return dtype


def current_watermark(engine, schema: str, table: str, column: str):
    if not warehouse.table_exists(engine, schema, table):
        return None
    return warehouse.scalar(engine, f"SELECT MAX({column}) FROM {schema}.{table}")


def _write_audit(engine, record: dict[str, Any]) -> None:
    schema = pipeline_config()["monitoring_schema"]
    df = pd.DataFrame([record])
    warehouse.write_dataframe(
        engine, df, "load_audit", schema, mode="append",
        dtype={"started_at": sa.DateTime(timezone=True), "finished_at": sa.DateTime(timezone=True),
               "watermark_before": sa.Text, "watermark_after": sa.Text},
    )


def load_source(
    source: str,
    engine=None,
    batch_id: str | None = None,
    data_dir: Path | None = None,
    run_id: str | None = None,
    full_refresh: bool = False,
) -> dict[str, Any]:
    """Extract -> drift check -> conform -> incremental filter -> load -> audit + volume check."""
    engine = engine or warehouse.get_engine()
    cfg = pipeline_config()
    src_cfg = cfg["sources"][source]
    schema = cfg["raw_schema"]
    batch_id = batch_id or uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc)
    checks: list[CheckResult] = []

    extracted = extract(source, data_dir)

    drift = check_dataframe(source, extracted.frame).to_check()
    drift.run_id = run_id
    checks.append(drift)
    if drift.failed:
        persist_results(checks, engine)
        raise SchemaDriftError(f"{source}: {drift.message}")

    frame, dropped = conform(source, extracted.frame)

    strategy = "full_refresh" if full_refresh else src_cfg["load_strategy"]
    wm_col = src_cfg["watermark_column"]
    wm_before = None
    if strategy == "incremental":
        wm_before = current_watermark(engine, schema, source, wm_col)
        if wm_before is not None:
            frame = frame[frame[wm_col] > pd.Timestamp(wm_before)]

    frame = frame.assign(
        _batch_id=batch_id,
        _loaded_at=pd.Timestamp(started),
        _source_file=extracted.path.name,
    )
    mode = "replace" if strategy == "full_refresh" else "append"
    rows = warehouse.write_dataframe(engine, frame, source, schema, mode=mode, dtype=_dtype_map(source))
    table_rows = int(warehouse.scalar(engine, f"SELECT COUNT(*) FROM {schema}.{source}"))
    wm_after = current_watermark(engine, schema, source, wm_col)

    volume = check_batch_volume(engine, source, rows, table_rows, strategy)
    volume.run_id = run_id
    checks.append(volume)

    finished = datetime.now(timezone.utc)
    _write_audit(engine, {
        "batch_id": batch_id,
        "run_id": run_id,
        "source": source,
        "load_strategy": strategy,
        "source_file": str(extracted.path),
        "file_checksum": extracted.checksum,
        "rows_extracted": len(extracted.frame),
        "rows_loaded": rows,
        "table_rows_after": table_rows,
        "watermark_before": None if wm_before is None else str(wm_before),
        "watermark_after": None if wm_after is None else str(wm_after),
        "columns_dropped": ",".join(dropped),
        "started_at": started,
        "finished_at": finished,
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "status": "success",
    })
    persist_results(checks, engine)
    log.info("Loaded %s: %d rows (%s), table now %d rows", source, rows, strategy, table_rows)
    return {
        "source": source,
        "batch_id": batch_id,
        "strategy": strategy,
        "rows_loaded": rows,
        "table_rows": table_rows,
        "columns_dropped": dropped,
        "checks": [c.to_dict() for c in checks],
    }


def load_all(engine=None, run_id: str | None = None, full_refresh: bool = False) -> list[dict[str, Any]]:
    batch_id = uuid.uuid4().hex[:12]
    return [load_source(s, engine, batch_id, run_id=run_id, full_refresh=full_refresh)
            for s in pipeline_config()["sources"]]
