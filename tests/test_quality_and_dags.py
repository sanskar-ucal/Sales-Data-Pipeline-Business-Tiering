import ast
from pathlib import Path

import pandas as pd

from sales_pipeline.quality.suites import all_suites
from sales_pipeline.quality.validate import summarize, validate_dataframe

DAG_DIR = Path(__file__).resolve().parents[1] / "airflow" / "dags"


def test_suites_build_and_detect_failures():
    spec = all_suites()["raw_order_items"]
    good = pd.DataFrame({"order_item_id": ["I1", "I2"], "order_id": ["O1", "O1"], "product_id": ["P1", "P2"],
                         "quantity": [1, 3], "unit_price": [9.5, 2.0],
                         "updated_at": pd.to_datetime(["2026-01-01", "2026-01-01"])})
    assert summarize("raw_order_items", spec, validate_dataframe(good, "ok", spec)).severity == "pass"
    bad = good.assign(quantity=[0, 3])
    check = summarize("raw_order_items", spec, validate_dataframe(bad, "bad", spec))
    assert check.severity == "error"
    assert check.details["failed_expectations"][0]["expectation"] == "expect_column_values_to_be_between"


def test_dags_are_valid_python_with_expected_ids():
    ids = set()
    for path in DAG_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "dag_id":
                ids.add(node.value.value)
    assert ids == {"sales_elt_pipeline", "data_observability"}
