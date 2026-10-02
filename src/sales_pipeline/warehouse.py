"""Warehouse adapter: PostgreSQL (default) or Snowflake behind one interface."""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.types import TypeEngine

from sales_pipeline.config import Settings, get_settings

log = logging.getLogger(__name__)

LOGICAL_TO_SQLA: dict[str, type[TypeEngine]] = {
    "string": sa.Text,
    "integer": sa.BigInteger,
    "float": sa.Float,
    "boolean": sa.Boolean,
    "date": sa.Date,
    "timestamp": sa.DateTime,
}


def build_url(settings: Settings) -> str:
    if settings.database_url:
        return settings.database_url
    if settings.warehouse_type == "snowflake":
        return (
            f"snowflake://{settings.sf_user}:{settings.sf_password}@{settings.sf_account}/"
            f"{settings.sf_database}?warehouse={settings.sf_warehouse}&role={settings.sf_role}"
        )
    return (
        f"postgresql+psycopg2://{settings.pg_user}:{settings.pg_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_database}"
    )


@lru_cache(maxsize=4)
def _engine_for(url: str) -> Engine:
    return sa.create_engine(url, pool_pre_ping=True, future=True)


def get_engine(settings: Settings | None = None) -> Engine:
    return _engine_for(build_url(settings or get_settings()))


def dialect(engine: Engine) -> str:
    return engine.dialect.name


def ensure_schema(engine: Engine, schema: str) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))


def execute(engine: Engine, sql: str, params: Mapping[str, Any] | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(sql), dict(params or {}))


def read_sql(engine: Engine, sql: str, params: Mapping[str, Any] | None = None) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(sa.text(sql), conn, params=dict(params or {}))


def scalar(engine: Engine, sql: str, params: Mapping[str, Any] | None = None) -> Any:
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), dict(params or {})).scalar()


def table_exists(engine: Engine, schema: str, table: str) -> bool:
    return sa.inspect(engine).has_table(table, schema=schema)


def get_columns(engine: Engine, schema: str, table: str) -> dict[str, TypeEngine]:
    """Return {column_name: sqlalchemy type} for an existing table (lower-cased names)."""
    return {c["name"].lower(): c["type"] for c in sa.inspect(engine).get_columns(table, schema=schema)}


def write_dataframe(
    engine: Engine,
    df: pd.DataFrame,
    table: str,
    schema: str,
    mode: str = "append",
    dtype: Mapping[str, TypeEngine | type[TypeEngine]] | None = None,
) -> int:
    """Write a DataFrame to ``schema.table``. ``mode`` is ``append`` or ``replace``.

    New columns present in ``df`` but missing from an existing table are added
    (additive schema evolution); callers are responsible for drift policy.
    """
    if mode not in {"append", "replace"}:
        raise ValueError(f"unsupported mode {mode!r}")
    ensure_schema(engine, schema)
    kind = dialect(engine)
    if kind == "snowflake":
        return _write_snowflake(engine, df, table, schema, mode)
    if kind == "postgresql":
        return _write_postgres(engine, df, table, schema, mode, dtype)
    df.to_sql(table, engine, schema=schema, if_exists=mode, index=False, dtype=dtype, chunksize=10_000)
    return len(df)


def _add_missing_columns(engine: Engine, df: pd.DataFrame, table: str, schema: str, dtype) -> None:
    existing = get_columns(engine, schema, table)
    missing = [c for c in df.columns if c.lower() not in existing]
    if not missing:
        return
    with engine.begin() as conn:
        for col in missing:
            col_type = (dtype or {}).get(col) or sa.Text
            col_type = col_type() if isinstance(col_type, type) else col_type
            ddl = col_type.compile(dialect=engine.dialect)
            log.warning("Adding column %s.%s.%s (%s)", schema, table, col, ddl)
            conn.execute(sa.text(f'ALTER TABLE {schema}.{table} ADD COLUMN "{col}" {ddl}'))


def _write_postgres(engine, df, table, schema, mode, dtype) -> int:
    if not table_exists(engine, schema, table):
        df.head(0).to_sql(table, engine, schema=schema, index=False, dtype=dtype)
    else:
        # TRUNCATE rather than DROP so dependent dbt views survive full refreshes.
        if mode == "replace":
            execute(engine, f"TRUNCATE TABLE {schema}.{table}")
        _add_missing_columns(engine, df, table, schema, dtype)
    if df.empty:
        return 0

    df = df.copy()
    for col in df.columns:
        if isinstance(df[col].dtype, pd.DatetimeTZDtype):
            df[col] = df[col].dt.tz_convert("UTC").dt.strftime("%Y-%m-%d %H:%M:%S.%f+00")
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="", date_format="%Y-%m-%d %H:%M:%S.%f")
    buf.seek(0)
    cols = ", ".join(f'"{c}"' for c in df.columns)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.copy_expert(f"COPY {schema}.{table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '')", buf)
            # Fresh planner statistics; without them downstream dbt joins can pick nested loops.
            cur.execute(f"ANALYZE {schema}.{table}")
        raw.commit()
    finally:
        raw.close()
    return len(df)


def _write_snowflake(engine, df, table, schema, mode) -> int:
    from snowflake.connector.pandas_tools import write_pandas  # optional dependency

    raw = engine.raw_connection()
    try:
        conn = getattr(raw, "driver_connection", None) or raw.connection
        ok, _, nrows, _ = write_pandas(
            conn,
            df,
            table_name=table.upper(),
            schema=schema.upper(),
            auto_create_table=True,
            overwrite=(mode == "replace"),
            quote_identifiers=False,
            use_logical_type=True,
        )
        if not ok:
            raise RuntimeError(f"write_pandas failed for {schema}.{table}")
        return int(nrows)
    finally:
        raw.close()
