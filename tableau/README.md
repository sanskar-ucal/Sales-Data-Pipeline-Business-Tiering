# Tableau Dashboards

The `publish_tableau` task exports every published dataset to `output/tableau/` as CSV and,
when `pantab` is installed, as `.hyper` extracts. If `TABLEAU_SERVER_URL` and a personal access
token are configured, the extracts are published to Tableau Server/Cloud as data sources (overwrite).
For live connections use `sales_dw_live.tds` (read-only `tableau_reader` role).

| Data source | Relation | Grain |
|---|---|---|
| customer_tiers | `marts.mart_customer_tiers` | customer |
| tier_summary | `marts.mart_tier_summary` | tier |
| tier_movement | `marts.mart_tier_movement` | as_of_date x from_tier x to_tier |
| customer_monthly_revenue | `marts.fct_customer_monthly` | customer x month |
| orders | `marts.fct_orders` | order |
| segment_profiles | `tiering.segment_profiles` | segment |
| tier_weights / validation_metrics / backtest_lift | `tiering.*` | feature / metric / tier |
| data_health | `marts.mart_data_health` | check x target |

## Dashboards

**1. Business Tiering Overview** (`customer_tiers`, `tier_summary`, `segment_profiles`)
- KPI tiles: active customers, 12m revenue, % revenue from Platinum + Gold.
- Tier pyramid: customers and revenue share by tier (dual axis).
- Pareto curve: cumulative revenue share vs customer rank, coloured by tier.
- Segment heatmap: segment x feature centroid values.
- Customer table with filters on region, industry, account manager, tier, segment, tier movement.

**2. Tier Migration** (`tier_movement`, `validation_metrics`, `tier_weights`, `backtest_lift`)
- Transition matrix (from_tier x to_tier) and upgrade/downgrade trend by run.
- Backtest lift by tier (mean future revenue, churn rate, high-value lift).
- Model health: Spearman, Kruskal-Wallis p, composite vs XGBoost AUC over time.
- Weight bar chart: expert vs entropy vs applied.

**3. Sales Performance** (`orders`, `customer_monthly_revenue`)
- Monthly revenue and margin trend with YoY; revenue by region, industry, channel.
- Late-payment rate and days-to-pay by tier.

**4. Data Health** (`data_health`)
- Status grid of check type x table coloured by last severity; 30-day pass rate.
- Freshness age vs SLA, row-count history.

## Tableau Prep flow (`Sales Tiering Prep`)

Use when analysts need a blended, Tableau-native extract without SQL:

1. **Input**: `customer_tiers.hyper`, `customer_monthly_revenue.hyper`, `segment_profiles.hyper`.
2. **Clean**: cast `month` to date, rename score columns to friendly names, group rare industries.
3. **Aggregate** monthly revenue to customer x quarter; **Pivot** quarters to columns for the trend sparkline.
4. **Join** tiers (left) to quarterly revenue on `customer_id`; **Join** segment profiles on `segment`.
5. **Calculated fields**: `Revenue Rank` (RANK by monetary_12m), `Is Top 20%`, `Tier Changed` (`previous_tier != tier`).
6. **Output**: publish `Sales Tiering (Prep)` data source to the `Sales Analytics` project; schedule after the Airflow publish task.
