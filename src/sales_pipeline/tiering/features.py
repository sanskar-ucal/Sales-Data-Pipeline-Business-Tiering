"""Point-in-time customer feature engineering.

All features are computed strictly from information available at ``as_of``,
so the same code serves production scoring and leakage-free backtests.

Inputs mirror the dbt marts:
  orders: order_id, customer_id, order_date, status, net_revenue, gross_margin,
          discount_pct, payment_terms_days, paid_date
  lines:  order_id, customer_id, order_date, status, category
  customers: customer_id, industry, region, employee_count, signup_date
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SCORE_FEATURES = [
    "monetary_12m", "frequency_12m", "recency_days", "gross_margin_pct", "revenue_growth",
    "category_breadth", "active_month_ratio", "late_payment_rate", "return_rate",
]
EXTRA_FEATURES = ["avg_order_value", "avg_discount", "tenure_days", "employee_count"]
MODEL_FEATURES = SCORE_FEATURES + EXTRA_FEATURES


def _dates(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c])
    return df


def frames_from_source_tables(tables: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build mart-shaped frames directly from source tables (mirrors the dbt models)."""
    orders = _dates(tables["orders"], ["order_date", "paid_date"])
    items = tables["order_items"].merge(tables["products"][["product_id", "category", "unit_cost"]], on="product_id")
    items = items.merge(orders[["order_id", "customer_id", "order_date", "status", "discount_pct"]], on="order_id")
    items["net_revenue"] = items["quantity"] * items["unit_price"] * (1 - items["discount_pct"])
    items["gross_margin"] = items["net_revenue"] - items["quantity"] * items["unit_cost"]
    agg = items.groupby("order_id").agg(net_revenue=("net_revenue", "sum"), gross_margin=("gross_margin", "sum"))
    orders = orders.merge(agg, on="order_id", how="inner")
    lines = items[["order_id", "customer_id", "order_date", "status", "category", "net_revenue"]]
    customers = _dates(tables["customers"], ["signup_date"])
    return orders, lines, customers


def build_features(
    orders: pd.DataFrame,
    lines: pd.DataFrame,
    customers: pd.DataFrame,
    as_of,
    lookback_days: int = 365,
    min_orders: int = 1,
) -> pd.DataFrame:
    as_of = pd.Timestamp(as_of).normalize()
    start = as_of - pd.Timedelta(days=lookback_days)
    mid = start + pd.Timedelta(days=lookback_days / 2)

    orders = _dates(orders, ["order_date", "paid_date"])
    lines = _dates(lines, ["order_date"])
    customers = _dates(customers, ["signup_date"])

    known = orders[orders["order_date"] <= as_of]
    window = known[known["order_date"] > start]
    completed = window[window["status"] == "completed"].assign(
        order_month=lambda d: d["order_date"].dt.to_period("M"))

    g = completed.groupby("customer_id")
    feats = pd.DataFrame({
        "monetary_12m": g["net_revenue"].sum(),
        "frequency_12m": g.size(),
        "gross_margin_sum": g["gross_margin"].sum(),
        "avg_discount": g["discount_pct"].mean(),
        "active_months": g["order_month"].nunique(),
        "rev_h1": completed[completed["order_date"] <= mid].groupby("customer_id")["net_revenue"].sum(),
        "rev_h2": completed[completed["order_date"] > mid].groupby("customer_id")["net_revenue"].sum(),
    })
    feats = feats[feats["frequency_12m"] >= min_orders]
    feats[["rev_h1", "rev_h2"]] = feats[["rev_h1", "rev_h2"]].fillna(0.0)

    last_order = known[known["status"] == "completed"].groupby("customer_id")["order_date"].max()
    feats["recency_days"] = (as_of - last_order.reindex(feats.index)).dt.days.astype(float)
    feats["avg_order_value"] = feats["monetary_12m"] / feats["frequency_12m"]
    feats["gross_margin_pct"] = np.where(
        feats["monetary_12m"] > 0, feats["gross_margin_sum"] / feats["monetary_12m"], 0.0)
    total = feats["rev_h1"] + feats["rev_h2"]
    feats["revenue_growth"] = np.where(total > 0, (feats["rev_h2"] - feats["rev_h1"]) / total, 0.0)
    feats["active_month_ratio"] = feats["active_months"] / max(lookback_days / 30.4, 1)

    win_lines = lines[(lines["order_date"] > start) & (lines["order_date"] <= as_of) & (lines["status"] == "completed")]
    feats["category_breadth"] = win_lines.groupby("customer_id")["category"].nunique().reindex(feats.index).fillna(0)

    settled = window[window["status"].isin(["completed", "returned"])].groupby("customer_id")
    returned = window[window["status"] == "returned"].groupby("customer_id").size()
    feats["return_rate"] = (returned.reindex(feats.index).fillna(0) / settled.size().reindex(feats.index)).fillna(0)

    feats["late_payment_rate"] = _late_payment_rate(completed, as_of).reindex(feats.index)

    cust = customers.set_index("customer_id")
    feats["tenure_days"] = (as_of - cust["signup_date"].reindex(feats.index)).dt.days.clip(lower=0).astype(float)
    feats["employee_count"] = pd.to_numeric(cust["employee_count"].reindex(feats.index), errors="coerce")
    for col in ("industry", "region"):
        if col in cust.columns:
            feats[col] = cust[col].reindex(feats.index)

    feats = feats.drop(columns=["gross_margin_sum", "active_months", "rev_h1", "rev_h2"])
    feats.index.name = "customer_id"
    feats["as_of_date"] = as_of
    return feats.reset_index()


def _late_payment_rate(completed: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Share of due invoices paid late (or still unpaid past due) as known at ``as_of``."""
    df = completed.copy()
    df["due_date"] = df["order_date"] + pd.to_timedelta(df["payment_terms_days"].astype(float), unit="D")
    paid_known = df["paid_date"].notna() & (df["paid_date"] <= as_of)
    due = df["due_date"] < as_of
    evaluable = paid_known | due
    late = (paid_known & (df["paid_date"] > df["due_date"])) | (~paid_known & due)
    df = df[evaluable].assign(late=late[evaluable].astype(float))
    return df.groupby("customer_id")["late"].mean()


def forward_labels(orders: pd.DataFrame, customer_ids, as_of, horizon_days: int,
                   high_value_quantile: float = 0.8) -> pd.DataFrame:
    """Outcomes in (as_of, as_of + horizon]: future revenue, churn, and high-value flag."""
    as_of = pd.Timestamp(as_of).normalize()
    end = as_of + pd.Timedelta(days=horizon_days)
    orders = _dates(orders, ["order_date"])
    fut = orders[(orders["order_date"] > as_of) & (orders["order_date"] <= end) & (orders["status"] == "completed")]
    g = fut.groupby("customer_id")
    out = pd.DataFrame(index=pd.Index(customer_ids, name="customer_id"))
    out["future_revenue"] = g["net_revenue"].sum().reindex(out.index).fillna(0.0)
    out["future_orders"] = g.size().reindex(out.index).fillna(0).astype(int)
    out["churned"] = (out["future_orders"] == 0).astype(int)
    threshold = out["future_revenue"].quantile(high_value_quantile)
    out["high_value"] = (out["future_revenue"] > threshold).astype(int)
    return out.reset_index()
