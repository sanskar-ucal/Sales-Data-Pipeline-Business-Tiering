"""Synthetic B2B sales source system.

Each customer has a latent "quality" that drives order frequency, basket size,
growth, churn, and payment behaviour, so tiering models have real signal to find.
Output files land in ``DATA_DIR`` and act as the extract source for the pipeline.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from sales_pipeline.config import get_settings

log = logging.getLogger(__name__)

INDUSTRIES = [
    "Retail", "Hospitality", "Healthcare", "Manufacturing",
    "Construction", "Education", "Technology", "Professional Services",
]
REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]
CATEGORIES = {
    "Beverages": (4, 25, 0.35),
    "Packaging": (2, 15, 0.30),
    "Cleaning Supplies": (5, 40, 0.40),
    "Office Supplies": (1, 30, 0.45),
    "Equipment": (150, 2500, 0.25),
    "Food Ingredients": (8, 60, 0.28),
    "Safety Gear": (10, 120, 0.38),
}
CHANNELS = ["Field Sales", "Inside Sales", "E-commerce", "Distributor"]
MANAGERS = [f"AM-{i:02d}" for i in range(1, 16)]
NAME_PREFIX = ["Blue", "Summit", "Pioneer", "Atlas", "Cedar", "Harbor", "Iron", "Maple", "North", "Silver",
               "Granite", "Evergreen", "Lakeside", "Prairie", "Redwood", "Coastal", "Union", "Keystone"]
NAME_SUFFIX = ["Holdings", "Group", "Partners", "Co", "Industries", "Supply", "Services", "Enterprises", "LLC"]


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def generate(
    n_customers: int = 1000,
    n_products: int = 80,
    start: date | None = None,
    end: date | None = None,
    seed: int = 7,
) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    end = end or date.today()
    start = start or (end - timedelta(days=3 * 365))
    end_ts = datetime.combine(end, datetime.min.time()) + timedelta(hours=23)

    # Products
    cats = rng.choice(list(CATEGORIES), size=n_products)
    lo = np.array([CATEGORIES[c][0] for c in cats], dtype=float)
    hi = np.array([CATEGORIES[c][1] for c in cats], dtype=float)
    margin = np.array([CATEGORIES[c][2] for c in cats]) + rng.normal(0, 0.06, n_products)
    list_price = np.round(np.exp(rng.uniform(np.log(lo), np.log(hi))), 2)
    products = pd.DataFrame({
        "product_id": [f"P{i:04d}" for i in range(1, n_products + 1)],
        "product_name": [f"{c} Item {i}" for i, c in enumerate(cats, 1)],
        "category": cats,
        "unit_cost": np.round(list_price * (1 - np.clip(margin, 0.08, 0.7)), 2),
        "list_price": list_price,
        "updated_at": end_ts - timedelta(days=30),
    })

    # Customers
    quality = rng.normal(0, 1, n_customers)
    employees = np.round(np.exp(rng.normal(3.5 + 0.5 * quality, 1.0))).astype(int) + 1
    span_days = (end - start).days
    signup_offset = rng.integers(-365, span_days - 60, n_customers)
    signup = np.array([start + timedelta(days=int(d)) for d in signup_offset])
    customers = pd.DataFrame({
        "customer_id": [f"C{i:05d}" for i in range(1, n_customers + 1)],
        "business_name": [
            f"{rng.choice(NAME_PREFIX)} {rng.choice(INDUSTRIES).split()[0]} {rng.choice(NAME_SUFFIX)}"
            for _ in range(n_customers)
        ],
        "industry": rng.choice(INDUSTRIES, n_customers),
        "region": rng.choice(REGIONS, n_customers, p=[0.24, 0.22, 0.2, 0.14, 0.2]),
        "employee_count": employees,
        "signup_date": signup,
        "account_manager": rng.choice(MANAGERS, n_customers),
        "updated_at": end_ts - timedelta(days=2),
    })
    customers.loc[rng.random(n_customers) < 0.03, "employee_count"] = np.nan
    customers["employee_count"] = customers["employee_count"].astype("Int64")

    # Orders: monthly Poisson process per customer with growth trend and churn.
    months = pd.date_range(start, end, freq="MS")
    base_rate = np.exp(0.2 + 0.55 * quality + 0.15 * np.log1p(employees) - 0.5)
    growth = rng.normal(0.15 * quality, 0.25)  # annual log-growth
    churn_prob = _sigmoid(-1.3 - 1.1 * quality)
    churned = rng.random(n_customers) < churn_prob
    churn_date = np.array([
        start + timedelta(days=int(rng.integers(span_days // 3, span_days))) if c else None for c in churned
    ])
    late_prob = _sigmoid(-1.6 - 0.9 * quality)
    discount_mu = np.clip(0.04 + 0.02 * quality, 0, 0.15)
    basket_mu = np.clip(2.2 + 0.8 * quality, 1, None)
    cat_affinity = rng.dirichlet(np.ones(len(CATEGORIES)) * 0.6, n_customers)
    cat_names = list(CATEGORIES)

    order_rows: list[tuple] = []
    item_rows: list[tuple] = []
    order_seq = 0
    item_seq = 0
    product_by_cat = {c: products.index[products["category"] == c].to_numpy() for c in cat_names}
    product_by_cat = {c: idx for c, idx in product_by_cat.items() if len(idx)}

    for ci in range(n_customers):
        cust_start = max(signup[ci], start)
        cust_end = min(churn_date[ci] or end, end)
        for m in months:
            m_start = m.date()
            m_end = min((m + pd.offsets.MonthEnd(0)).date(), cust_end)
            if m_end < cust_start or m_start > cust_end:
                continue
            years_in = (m_start - start).days / 365.0
            seasonal = 1 + 0.15 * np.sin(2 * np.pi * (m.month - 3) / 12)
            lam = base_rate[ci] * np.exp(growth[ci] * years_in) * seasonal
            n = rng.poisson(lam)
            lo_d = max(m_start, cust_start)
            window = (m_end - lo_d).days + 1
            if n == 0 or window <= 0:
                continue
            for _ in range(n):
                order_seq += 1
                odate = lo_d + timedelta(days=int(rng.integers(0, window)))
                status = rng.choice(["completed", "cancelled", "returned"], p=[0.93, 0.04, 0.03])
                terms = int(rng.choice([15, 30, 45, 60], p=[0.2, 0.5, 0.2, 0.1]))
                late = rng.random() < late_prob[ci]
                days_to_pay = terms + (int(rng.integers(5, 60)) if late else -int(rng.integers(0, 10)))
                paid = odate + timedelta(days=max(days_to_pay, 0))
                paid_date = paid if (status == "completed" and paid <= end) else None
                updated = datetime.combine(paid_date or odate, datetime.min.time()) + timedelta(
                    hours=int(rng.integers(8, 20)), minutes=int(rng.integers(0, 60)))
                updated = min(updated, end_ts)
                order_id = f"O{order_seq:07d}"
                order_rows.append((
                    order_id, customers.customer_id.iat[ci], odate, status,
                    rng.choice(CHANNELS, p=[0.35, 0.3, 0.25, 0.1]),
                    round(float(np.clip(rng.normal(discount_mu[ci], 0.03), 0, 0.3)), 3),
                    terms, paid_date, updated,
                ))
                n_items = max(1, rng.poisson(basket_mu[ci]))
                for _ in range(n_items):
                    item_seq += 1
                    cat = rng.choice(cat_names, p=cat_affinity[ci])
                    pool = product_by_cat.get(cat)
                    pidx = rng.choice(pool) if pool is not None else rng.integers(0, n_products)
                    price = products.list_price.iat[pidx]
                    qty = int(max(1, np.round(rng.lognormal(1.6 + 0.3 * quality[ci], 0.7)
                                              / (1 + np.log1p(price) / 4))))
                    item_rows.append((
                        f"I{item_seq:08d}", order_id, products.product_id.iat[pidx], qty,
                        round(float(price * rng.uniform(0.95, 1.05)), 2), updated,
                    ))

    orders = pd.DataFrame(order_rows, columns=[
        "order_id", "customer_id", "order_date", "status", "channel",
        "discount_pct", "payment_terms_days", "paid_date", "updated_at",
    ])
    order_items = pd.DataFrame(item_rows, columns=[
        "order_item_id", "order_id", "product_id", "quantity", "unit_price", "updated_at",
    ])
    log.info("Generated %d customers, %d products, %d orders, %d order items",
             len(customers), len(products), len(orders), len(order_items))
    return {"customers": customers, "products": products, "orders": orders, "order_items": order_items}


def inject_schema_drift(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Simulate upstream changes: an added column and a type change."""
    orders = tables["orders"].copy()
    orders["sales_rep_id"] = "SR-000"
    orders["payment_terms_days"] = orders["payment_terms_days"].astype(str) + " days"
    return {**tables, "orders": orders}


def write(tables: dict[str, pd.DataFrame], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        log.info("Wrote %s (%d rows)", path, len(df))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customers", type=int, default=1000)
    parser.add_argument("--products", type=int, default=80)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--inject-schema-drift", action="store_true")
    args = parser.parse_args(argv)

    end = args.end_date or date.today()
    tables = generate(args.customers, args.products, end - timedelta(days=int(args.years * 365)), end, args.seed)
    if args.inject_schema_drift:
        tables = inject_schema_drift(tables)
    write(tables, args.out or get_settings().data_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
