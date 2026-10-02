"""Publish analytical datasets for Tableau: CSV/Hyper extracts and Tableau Server/Cloud publishing."""

from __future__ import annotations

import logging
from pathlib import Path

from sales_pipeline import warehouse
from sales_pipeline.config import get_settings

log = logging.getLogger(__name__)

# Published datasets: name -> warehouse relation. Mirrored by dbt exposures.
DATASETS = {
    "customer_tiers": "marts.mart_customer_tiers",
    "tier_summary": "marts.mart_tier_summary",
    "tier_movement": "marts.mart_tier_movement",
    "customer_monthly_revenue": "marts.fct_customer_monthly",
    "orders": "marts.fct_orders",
    "segment_profiles": "tiering.segment_profiles",
    "tier_weights": "tiering.tier_weights",
    "backtest_lift": "tiering.backtest_lift",
    "validation_metrics": "tiering.validation_metrics",
    "data_health": "marts.mart_data_health",
}


def export_extracts(engine=None, out_dir: Path | None = None, hyper: bool = True) -> dict[str, list[str]]:
    engine = engine or warehouse.get_engine()
    out_dir = out_dir or (get_settings().output_dir / "tableau")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import pantab  # optional dependency
    except ImportError:
        pantab = None
        if hyper:
            log.info("pantab not installed; writing CSV extracts only")

    written: dict[str, list[str]] = {"csv": [], "hyper": []}
    for name, relation in DATASETS.items():
        schema, table = relation.split(".")
        if not warehouse.table_exists(engine, schema, table):
            log.warning("Skipping %s: %s does not exist", name, relation)
            continue
        df = warehouse.read_sql(engine, f"SELECT * FROM {relation}")
        csv_path = out_dir / f"{name}.csv"
        df.to_csv(csv_path, index=False)
        written["csv"].append(str(csv_path))
        if hyper and pantab is not None:
            hyper_path = out_dir / f"{name}.hyper"
            pantab.frame_to_hyper(df, hyper_path, table=name)
            written["hyper"].append(str(hyper_path))
        log.info("Exported %s (%d rows)", name, len(df))
    return written


def publish_to_server(hyper_files: list[str]) -> list[str]:
    """Publish .hyper extracts as data sources to Tableau Server/Cloud (overwrite)."""
    s = get_settings()
    if not (s.tableau_server_url and s.tableau_token_name and s.tableau_token_secret):
        log.info("Tableau Server not configured; skipping publish")
        return []
    import tableauserverclient as TSC  # optional dependency

    auth = TSC.PersonalAccessTokenAuth(s.tableau_token_name, s.tableau_token_secret, site_id=s.tableau_site)
    server = TSC.Server(s.tableau_server_url, use_server_version=True)
    published = []
    with server.auth.sign_in(auth):
        projects = [p for p in TSC.Pager(server.projects) if p.name == s.tableau_project]
        if not projects:
            raise RuntimeError(f"Tableau project {s.tableau_project!r} not found")
        for path in hyper_files:
            ds = TSC.DatasourceItem(projects[0].id, name=Path(path).stem)
            item = server.datasources.publish(ds, path, mode=TSC.Server.PublishMode.Overwrite)
            published.append(item.name)
            log.info("Published data source %s", item.name)
    return published


def publish(engine=None) -> dict[str, list[str]]:
    written = export_extracts(engine)
    written["published"] = publish_to_server(written["hyper"])
    return written
