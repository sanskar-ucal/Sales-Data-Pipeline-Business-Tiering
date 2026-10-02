"""Schema-drift detection against data contracts (config/schema_contracts.yml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd
import sqlalchemy as sa

from sales_pipeline import warehouse
from sales_pipeline.config import pipeline_config, schema_contracts
from sales_pipeline.monitoring.results import CheckResult, worst

_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:?\d{2}|Z)?$")
_BOOL = {"true", "false", "t", "f", "yes", "no"}

# observed type -> contract types it can safely load into
COMPATIBLE = {
    "integer": {"integer", "float", "string"},
    "float": {"float", "string"},
    "boolean": {"boolean", "string"},
    "date": {"date", "timestamp", "string"},
    "timestamp": {"timestamp", "string"},
    "string": {"string"},
    "unknown": {"integer", "float", "boolean", "date", "timestamp", "string"},
}


def infer_logical_type(series: pd.Series, sample: int = 5000) -> str:
    """Infer the logical type of a column from raw (string) or typed values."""
    s = series.dropna()
    if s.empty:
        return "unknown"
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        return "integer" if (s == s.round()).all() else "float"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "timestamp"
    values = s.astype(str).str.strip()
    values = values[values != ""].head(sample)
    if values.empty:
        return "unknown"
    for name, test in (
        ("integer", lambda v: v.str.match(_INT)),
        ("float", lambda v: v.str.match(_FLOAT)),
        ("boolean", lambda v: v.str.lower().isin(_BOOL)),
        ("date", lambda v: v.str.match(_DATE)),
        ("timestamp", lambda v: v.str.match(_TS)),
    ):
        if test(values).all():
            return name
    return "string"


def sqla_to_logical(col_type: sa.types.TypeEngine) -> str:
    if isinstance(col_type, sa.Boolean):
        return "boolean"
    if isinstance(col_type, sa.Integer):
        return "integer"
    if isinstance(col_type, (sa.Float, sa.Numeric)):
        scale = getattr(col_type, "scale", None)
        return "integer" if (isinstance(col_type, sa.Numeric) and scale == 0) else "float"
    if isinstance(col_type, sa.DateTime):
        return "timestamp"
    if isinstance(col_type, sa.Date):
        return "date"
    return "string"


@dataclass
class DriftReport:
    target: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    type_changes: dict[str, tuple[str, str]] = field(default_factory=dict)  # col -> (expected, observed)

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.type_changes)

    def to_check(self, policy: dict | None = None) -> CheckResult:
        policy = policy or pipeline_config()["schema_drift"]
        severities = []
        parts = []
        if self.added:
            severities.append(policy["on_added_column"])
            parts.append(f"added columns {self.added}")
        if self.removed:
            severities.append(policy["on_removed_column"])
            parts.append(f"removed columns {self.removed}")
        if self.type_changes:
            severities.append(policy["on_type_change"])
            parts.append("type changes " + ", ".join(
                f"{c}: {e}->{o}" for c, (e, o) in self.type_changes.items()))
        return CheckResult(
            check_type="schema_drift",
            check_name="contract_match",
            target=self.target,
            severity=worst(severities),
            message="; ".join(parts) if parts else "schema matches contract",
            observed_value=float(len(self.added) + len(self.removed) + len(self.type_changes)),
            threshold=0.0,
            details={"added": self.added, "removed": self.removed,
                     "type_changes": {k: list(v) for k, v in self.type_changes.items()}},
        )


def compare(target: str, observed: dict[str, str], contract: dict[str, dict]) -> DriftReport:
    observed = {k.lower(): v for k, v in observed.items() if not k.startswith("_")}
    expected = {k.lower(): v["type"] for k, v in contract.items()}
    report = DriftReport(target=target)
    report.added = sorted(set(observed) - set(expected))
    report.removed = sorted(set(expected) - set(observed))
    for col in sorted(set(observed) & set(expected)):
        if expected[col] not in COMPATIBLE[observed[col]]:
            report.type_changes[col] = (expected[col], observed[col])
    return report


def check_dataframe(source: str, df: pd.DataFrame, contract: dict | None = None) -> DriftReport:
    contract = contract or schema_contracts()[source]
    observed = {col: infer_logical_type(df[col]) for col in df.columns}
    return compare(f"source.{source}", observed, contract)


def check_warehouse_table(engine, source: str, schema: str | None = None) -> DriftReport:
    schema = schema or pipeline_config()["raw_schema"]
    contract = schema_contracts()[source]
    target = f"{schema}.{source}"
    if not warehouse.table_exists(engine, schema, source):
        return DriftReport(target=target, removed=sorted(contract))
    observed = {c: sqla_to_logical(t) for c, t in warehouse.get_columns(engine, schema, source).items()}
    return compare(target, observed, contract)
