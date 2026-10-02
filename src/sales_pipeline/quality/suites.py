"""Great Expectations suite definitions, declared as data and built into GX objects.

Each suite targets a warehouse relation. ``severity`` controls whether a failed
suite should fail the pipeline (``error``) or only alert (``warn``).
"""

from __future__ import annotations

from typing import Any

from sales_pipeline.config import schema_contracts, tiering_config

ORDER_STATUSES = ["completed", "cancelled", "returned"]
CHANNELS = ["Field Sales", "Inside Sales", "E-commerce", "Distributor"]


def _contract_columns(source: str) -> list[str]:
    return list(schema_contracts()[source])


def raw_suites() -> dict[str, dict[str, Any]]:
    return {
        "raw_customers": {
            "relation": "raw.customers",
            "severity": "error",
            "expectations": [
                ("ExpectTableRowCountToBeBetween", {"min_value": 1}),
                ("ExpectTableColumnsToMatchSet", {"column_set": _contract_columns("customers"), "exact_match": False}),
                ("ExpectColumnValuesToNotBeNull", {"column": "customer_id"}),
                ("ExpectColumnValuesToBeUnique", {"column": "customer_id"}),
                ("ExpectColumnValuesToMatchRegex", {"column": "customer_id", "regex": r"^C\d{5}$"}),
                ("ExpectColumnValuesToNotBeNull", {"column": "industry"}),
                ("ExpectColumnValuesToBeBetween", {"column": "employee_count", "min_value": 1, "max_value": 1_000_000,
                                                   "mostly": 0.99}),
                ("ExpectColumnValuesToNotBeNull", {"column": "employee_count", "mostly": 0.9}),
            ],
        },
        "raw_products": {
            "relation": "raw.products",
            "severity": "error",
            "expectations": [
                ("ExpectTableRowCountToBeBetween", {"min_value": 1}),
                ("ExpectColumnValuesToBeUnique", {"column": "product_id"}),
                ("ExpectColumnValuesToBeBetween", {"column": "list_price", "min_value": 0, "strict_min": True}),
                ("ExpectColumnValuesToBeBetween", {"column": "unit_cost", "min_value": 0}),
                ("ExpectColumnPairValuesAToBeGreaterThanB", {"column_A": "list_price", "column_B": "unit_cost",
                                                             "or_equal": True, "mostly": 0.98}),
            ],
        },
        "raw_orders": {
            "relation": "raw.orders",
            "severity": "error",
            "expectations": [
                ("ExpectTableRowCountToBeBetween", {"min_value": 1}),
                ("ExpectTableColumnsToMatchSet", {"column_set": _contract_columns("orders"), "exact_match": False}),
                ("ExpectColumnValuesToNotBeNull", {"column": "order_id"}),
                ("ExpectCompoundColumnsToBeUnique", {"column_list": ["order_id", "updated_at"]}),
                ("ExpectColumnValuesToNotBeNull", {"column": "customer_id"}),
                ("ExpectColumnValuesToBeInSet", {"column": "status", "value_set": ORDER_STATUSES}),
                ("ExpectColumnValuesToBeInSet", {"column": "channel", "value_set": CHANNELS}),
                ("ExpectColumnValuesToBeBetween", {"column": "discount_pct", "min_value": 0, "max_value": 0.5}),
                ("ExpectColumnValuesToBeInSet", {"column": "payment_terms_days", "value_set": [0, 15, 30, 45, 60, 90]}),
                ("ExpectColumnPairValuesAToBeGreaterThanB", {"column_A": "paid_date", "column_B": "order_date",
                                                             "or_equal": True, "ignore_row_if": "either_value_is_missing"}),
            ],
        },
        "raw_order_items": {
            "relation": "raw.order_items",
            "severity": "error",
            "expectations": [
                ("ExpectTableRowCountToBeBetween", {"min_value": 1}),
                ("ExpectCompoundColumnsToBeUnique", {"column_list": ["order_item_id", "updated_at"]}),
                ("ExpectColumnValuesToNotBeNull", {"column": "order_id"}),
                ("ExpectColumnValuesToNotBeNull", {"column": "product_id"}),
                ("ExpectColumnValuesToBeBetween", {"column": "quantity", "min_value": 1, "max_value": 10_000}),
                ("ExpectColumnValuesToBeBetween", {"column": "unit_price", "min_value": 0, "strict_min": True}),
            ],
        },
    }


def mart_suites() -> dict[str, dict[str, Any]]:
    return {
        "marts_fct_orders": {
            "relation": "marts.fct_orders",
            "severity": "error",
            "expectations": [
                ("ExpectTableRowCountToBeBetween", {"min_value": 1}),
                ("ExpectColumnValuesToBeUnique", {"column": "order_id"}),
                ("ExpectColumnValuesToNotBeNull", {"column": "customer_id"}),
                ("ExpectColumnValuesToBeBetween", {"column": "net_revenue", "min_value": 0}),
                ("ExpectColumnValuesToBeBetween", {"column": "gross_margin_pct", "min_value": -1, "max_value": 1,
                                                   "mostly": 0.99}),
                ("ExpectColumnMeanToBeBetween", {"column": "net_revenue", "min_value": 1}),
            ],
        },
        "marts_dim_customers": {
            "relation": "marts.dim_customers",
            "severity": "error",
            "expectations": [
                ("ExpectColumnValuesToBeUnique", {"column": "customer_id"}),
                ("ExpectColumnValuesToBeBetween", {"column": "lifetime_revenue", "min_value": 0}),
            ],
        },
        "marts_fct_customer_monthly": {
            "relation": "marts.fct_customer_monthly",
            "severity": "warn",
            "expectations": [
                ("ExpectCompoundColumnsToBeUnique", {"column_list": ["customer_id", "month"]}),
                ("ExpectColumnValuesToBeBetween", {"column": "net_revenue", "min_value": 0}),
            ],
        },
    }


def tiering_suites() -> dict[str, dict[str, Any]]:
    tier_names = [t["name"] for t in tiering_config()["tiers"]]
    return {
        "tiering_customer_tiers": {
            "relation": "tiering.customer_tiers",
            "severity": "error",
            "expectations": [
                ("ExpectTableRowCountToBeBetween", {"min_value": 1}),
                ("ExpectColumnValuesToBeUnique", {"column": "customer_id"}),
                ("ExpectColumnValuesToBeBetween", {"column": "composite_score", "min_value": 0, "max_value": 100}),
                ("ExpectColumnValuesToBeInSet", {"column": "tier", "value_set": tier_names}),
                ("ExpectColumnDistinctValuesToEqualSet", {"column": "tier", "value_set": tier_names}),
                ("ExpectColumnValuesToNotBeNull", {"column": "segment"}),
            ],
        },
    }


def all_suites() -> dict[str, dict[str, Any]]:
    return {**raw_suites(), **mart_suites(), **tiering_suites()}


LAYERS = {"raw": raw_suites, "marts": mart_suites, "tiering": tiering_suites}
