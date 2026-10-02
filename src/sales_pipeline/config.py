"""Runtime settings (environment) and YAML configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(os.getenv("PIPELINE_HOME", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.getenv("PIPELINE_CONFIG_DIR", PROJECT_ROOT / "config"))


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    warehouse_type: str = field(default_factory=lambda: _env("WAREHOUSE_TYPE", "postgres").lower())

    pg_host: str = field(default_factory=lambda: _env("PG_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: int(_env("PG_PORT", "5432")))
    pg_user: str = field(default_factory=lambda: _env("PG_USER", "pipeline"))
    pg_password: str = field(default_factory=lambda: _env("PG_PASSWORD", "pipeline"))
    pg_database: str = field(default_factory=lambda: _env("PG_DATABASE", "sales_dw"))

    sf_account: str | None = field(default_factory=lambda: _env("SNOWFLAKE_ACCOUNT"))
    sf_user: str | None = field(default_factory=lambda: _env("SNOWFLAKE_USER"))
    sf_password: str | None = field(default_factory=lambda: _env("SNOWFLAKE_PASSWORD"))
    sf_role: str | None = field(default_factory=lambda: _env("SNOWFLAKE_ROLE"))
    sf_warehouse: str | None = field(default_factory=lambda: _env("SNOWFLAKE_WAREHOUSE"))
    sf_database: str | None = field(default_factory=lambda: _env("SNOWFLAKE_DATABASE"))

    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(PROJECT_ROOT / "data" / "raw"))))
    output_dir: Path = field(default_factory=lambda: Path(_env("OUTPUT_DIR", str(PROJECT_ROOT / "output"))))

    slack_webhook_url: str | None = field(default_factory=lambda: _env("SLACK_WEBHOOK_URL"))
    smtp_host: str | None = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str | None = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str | None = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    alert_email_from: str | None = field(default_factory=lambda: _env("ALERT_EMAIL_FROM"))
    alert_email_to: str | None = field(default_factory=lambda: _env("ALERT_EMAIL_TO"))

    tableau_server_url: str | None = field(default_factory=lambda: _env("TABLEAU_SERVER_URL"))
    tableau_site: str = field(default_factory=lambda: _env("TABLEAU_SITE", ""))
    tableau_token_name: str | None = field(default_factory=lambda: _env("TABLEAU_TOKEN_NAME"))
    tableau_token_secret: str | None = field(default_factory=lambda: _env("TABLEAU_TOKEN_SECRET"))
    tableau_project: str = field(default_factory=lambda: _env("TABLEAU_PROJECT", "Sales Analytics"))

    database_url: str | None = field(default_factory=lambda: _env("DATABASE_URL"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@cache
def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def pipeline_config() -> dict[str, Any]:
    return load_yaml("pipeline.yml")


def schema_contracts() -> dict[str, dict[str, dict[str, Any]]]:
    return load_yaml("schema_contracts.yml")


def tiering_config() -> dict[str, Any]:
    return load_yaml("tiering.yml")
