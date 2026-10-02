# Sales Data Pipeline & Business Tiering

**Python · Airflow · PostgreSQL / Snowflake · dbt · Great Expectations · scikit-learn · XGBoost · Tableau**

An ELT pipeline that loads B2B sales data into a warehouse, transforms it with dbt, checks data quality with Great Expectations, monitors freshness, volume, and schema drift with alerting, and scores every customer into a business tier (Platinum / Gold / Silver / Bronze). The tiers are validated with hypothesis tests and benchmarked against ML baselines. The results are published as Tableau datasets.

```
CSV landing zone ─► Airflow extract/load ─► raw ─► dbt staging ─► dbt marts ─► Python tiering ─► dbt published marts ─► Tableau
                     │ schema contracts        │ GX suites       │ dbt tests      │ GX + stats tests                    │ .hyper / Server
                     └── freshness · volume · schema drift ──► monitoring.check_results ──► Slack / email alerts
```

## Pipeline (Airflow DAG `sales_elt_pipeline`, daily)

| Step | What happens |
|---|---|
| `check_source_contracts` | Every landed file is checked against `config/schema_contracts.yml`. Removed columns or changed types fail the run before anything is loaded. |
| `extract_load` (one mapped task per source) | Incremental loads (orders, order_items) use an `updated_at` watermark; dimension tables are fully refreshed. Rows are written to Postgres with `COPY` (Snowflake uses `write_pandas`). Each row gets `_batch_id`, `_loaded_at`, and `_source_file`. Each batch is logged to `monitoring.load_audit`, and its size is checked for volume anomalies. |
| `validate_raw` | Great Expectations suites for the raw layer: keys, uniqueness, accepted values, ranges, column sets. |
| `dbt_source_freshness` → `dbt_build_core` | Staging (deduplicated to the latest version of each record), intermediate models, and marts (`fct_orders`, `fct_order_lines`, `fct_customer_monthly`, `dim_customers`, `dim_products`, `mart_data_health`). Includes 58 dbt tests and a customer SCD2 snapshot. |
| `validate_marts` | Great Expectations suites for the marts. |
| `run_tiering` → `validate_tiering` | Feature engineering, composite score, tiers, segments, and a backtest. Results are written to the `tiering.*` tables plus a validation report. |
| `dbt_build_post_tiering` | `mart_customer_tiers`, `mart_tier_summary`, `mart_tier_movement`, plus their tests. |
| `publish_tableau`, `dbt_docs_generate`, `post_run_monitoring` | Tableau extracts and publishing, lineage docs, and a final health check. |

Any task failure triggers a Slack/email alert through `on_failure_callback`, and there is an SLA miss callback. A second DAG, `data_observability`, runs hourly. It covers freshness (both the age of the newest business record and the time since the last load), volume anomalies (z-score plus percentage change against the last 14 loads, and tables that shrink), and drift between the warehouse schema and the contract. Every result goes to `monitoring.check_results`, which feeds the Data Health dashboard.

Pipeline code runs in its own virtualenv (`@task.external_python`) and dbt runs in another, because pandas 2.2+ and SQLAlchemy 2 conflict with Airflow 2's pinned dependencies.

## Tiering model (`src/sales_pipeline/tiering`)

- **Features** are computed as of a given date with no look-ahead, over a 365-day window: 12-month revenue (`monetary_12m`), order count (`frequency_12m`), `recency_days`, gross margin %, revenue growth (second half of the window vs the first half), category breadth, share of active months, late-payment rate, return rate, plus tenure, average discount, and employee count.
- **Composite score**: features are imputed, log-transformed where configured, winsorized at the 1st/99th percentile, and oriented so higher is always better. They are then normalized (percentile, min-max, or z-score) and combined as a weighted sum on a 0–100 scale. Weights can be the configured expert weights, entropy weights, or a blend of both.
- **Segmentation**: tiers come from score-percentile cut-offs (10/20/30/40%). Behavioral segments come from k-means, with k chosen by silhouette score, and are auto-labeled (Champions, Rising Stars, At Risk, ...).
- **Validation**: a backtest scores customers 180 days ago and compares the tiers with what actually happened over the next 180 days:
  - Kruskal-Wallis with epsilon², and one-way ANOVA on log revenue.
  - One-sided Mann-Whitney U tests between adjacent tiers, with Holm correction.
  - Chi-square of tier against churn, with Cramér's V.
  - Spearman correlation between score and future revenue, with a bootstrap 95% CI.
  - Lift table, tier stability (quadratic-weighted kappa, PSI), and weight sensitivity (±20% perturbations).
- **Baselines**: logistic regression, random forest, and XGBoost predict top-20% future revenue, scored out-of-fold. A paired bootstrap test compares the best model's AUC with the composite score's, and the model's feature importances are compared with the configured weights.

Sample results (synthetic data, [full report](docs/sample_output/tiering_validation_report.md)): Platinum customers (10% of accounts) generated 55.6% of next-period revenue, with 3.8x lift. Spearman ρ = 0.79 (95% CI 0.76 to 0.82). Kruskal-Wallis p ≈ 1e-100. All adjacent tier pairs differ significantly. The composite score reaches an AUC of 0.90, against 0.95–0.96 for the ML baselines, so it gives up a few AUC points in exchange for transparent, configurable weights.

## Quick start

**Docker (Airflow + Postgres):**
```bash
cp .env.example .env
docker compose up -d --build        # Airflow UI http://localhost:8080 (admin/admin)
# unpause and trigger `sales_elt_pipeline`
```

**Local (no Airflow):**
```bash
make install                                         # package + dbt
export PG_HOST=localhost PG_USER=pipeline PG_PASSWORD=pipeline PG_DATABASE=sales_dw
make data                                            # synthetic sources -> data/raw
make run-all                                         # same sequence as the DAG
python -m sales_pipeline generate-data --inject-schema-drift && make load   # see drift detection fail the load
make test
```

**Snowflake:** set `WAREHOUSE_TYPE=snowflake`, `DBT_TARGET=snowflake`, and the `SNOWFLAKE_*` variables. Install with `pip install -e ".[snowflake]"`. The dbt models use cross-database macros, so the same SQL runs on both warehouses.

## Repository layout

```
airflow/dags/          sales_elt_pipeline.py, data_observability.py
config/                pipeline.yml (sources, SLAs, thresholds), schema_contracts.yml, tiering.yml
dbt/                   models (staging / intermediate / marts / marts/tiering), tests, snapshots, exposures
src/sales_pipeline/    ingestion/, monitoring/, quality/ (GX), tiering/, publish/ (Tableau), cli.py
tableau/               dashboard specs, Tableau Prep flow, live .tds data source
docs/                  lineage.md (lineage + metadata catalog), sample_output/
tests/                 unit tests + Postgres integration test
docker/, docker-compose.yml, .github/workflows/ci.yml
```

See [docs/lineage.md](docs/lineage.md) for lineage and metadata, and [tableau/README.md](tableau/README.md) for the dashboards.
