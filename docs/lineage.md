# Data Lineage & Metadata

## End-to-end lineage

```mermaid
flowchart LR
    subgraph Source["Landing zone (CSV)"]
        C[customers.csv] & P[products.csv] & O[orders.csv] & I[order_items.csv]
    end
    subgraph Raw["raw schema (Airflow load)"]
        RC[raw.customers] & RP[raw.products] & RO[raw.orders] & RI[raw.order_items]
    end
    subgraph Staging["staging (dbt)"]
        SC[stg_customers] & SP[stg_products] & SO[stg_orders] & SI[stg_order_items]
        IOL[int_order_lines] --> IOT[int_order_totals]
    end
    subgraph Marts["marts (dbt)"]
        FO[fct_orders] & FOL[fct_order_lines] & FCM[fct_customer_monthly] & DC[dim_customers] & DP[dim_products]
    end
    subgraph Tiering["tiering (Python)"]
        CT[customer_tiers] & CTH[customer_tier_history] & SPR[segment_profiles] & VM[validation_metrics]
    end
    subgraph Published["published marts (dbt, tag:post_tiering)"]
        MCT[mart_customer_tiers] & MTS[mart_tier_summary] & MTM[mart_tier_movement]
    end
    subgraph BI["Tableau"]
        D1[Business Tiering] & D2[Tier Migration] & D3[Sales Performance] & D4[Data Health]
    end
    C-->RC-->SC; P-->RP-->SP; O-->RO-->SO; I-->RI-->SI
    SI & SO & SP --> IOL
    IOL --> FOL; SO & IOT --> FO --> FCM; SC & FO --> DC; SP & IOL --> DP
    FO & FOL & DC --> CT --> CTH
    CT & DC & CTH --> MCT --> MTS; CTH --> MTM
    MCT & MTS & SPR --> D1; MTM & VM --> D2; FO & FCM & DC --> D3
    MON[(monitoring.check_results / load_audit)] --> MDH[mart_data_health] --> D4
```

## Where lineage and metadata live

| Artifact | What it captures | How to view |
|---|---|---|
| dbt docs (`dbt docs generate`) | Model/source/test DAG, column descriptions, Tableau exposures | `make docs` -> http://localhost:8080 |
| dbt `persist_docs` | Model and column descriptions written as Postgres/Snowflake comments | Any SQL client / Tableau field descriptions |
| `monitoring.load_audit` | Per-batch source file, checksum, rows, watermarks, duration | SQL / Data Health dashboard |
| `monitoring.check_results` | Every freshness, volume, drift and GX result with run_id | SQL / Data Health dashboard |
| `_batch_id`, `_loaded_at`, `_source_file` | Row-level provenance on every raw table | SQL |
| `tiering.*` `run_id` / `as_of_date` | Which run and as-of date produced each tier | SQL |
| `snapshots.customers_snapshot` | SCD2 history of customer attributes | SQL |
| Airflow Datasets + OpenLineage provider | Task-level lineage (enable in `docker-compose.yml`) | Airflow Datasets view / Marquez |
| `config/schema_contracts.yml` | Data contracts for each source | Repo |

## Ownership

| Layer | Owner | SLA |
|---|---|---|
| raw | Data Engineering | loaded by 06:30 daily; freshness warn 26h / error 50h |
| staging, marts | Analytics Engineering | built by 07:00 daily |
| tiering | Analytics / Data Science | scored daily; validated on each run |
| Tableau extracts | BI | refreshed after `publish_tableau` |
