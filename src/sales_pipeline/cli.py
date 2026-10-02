"""Command-line entry point: run any pipeline stage without Airflow."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys

from sales_pipeline.config import PROJECT_ROOT


def _dbt(*args: str) -> None:
    cmd = ["dbt", *args, "--project-dir", str(PROJECT_ROOT / "dbt"), "--profiles-dir", str(PROJECT_ROOT / "dbt")]
    logging.info("Running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def cmd_generate(args) -> None:
    from sales_pipeline import data_generator

    argv = ["--customers", str(args.customers), "--years", str(args.years), "--seed", str(args.seed)]
    if args.inject_schema_drift:
        argv.append("--inject-schema-drift")
    data_generator.main(argv)


def cmd_load(args) -> None:
    from sales_pipeline.ingestion.load import load_all

    print(json.dumps(load_all(full_refresh=args.full_refresh), indent=2, default=str))


def cmd_validate(args) -> None:
    from sales_pipeline.quality.validate import run_layer

    checks = run_layer(args.layer, raise_on_error=not args.no_fail)
    for c in checks:
        print(f"[{c.severity.upper():5}] {c.check_name}: {c.message}")


def cmd_monitor(args) -> None:
    from sales_pipeline.monitoring.runner import run_all_checks

    for c in run_all_checks(alert=not args.no_alert):
        print(f"[{c.severity.upper():5}] {c.check_type:13} {c.target:20} {c.message}")


def cmd_tier(args) -> None:
    from sales_pipeline.tiering.pipeline import run

    print(json.dumps(run(as_of=args.as_of, write=not args.dry_run), indent=2, default=str))


def cmd_publish(args) -> None:
    from sales_pipeline.publish.tableau import publish

    print(json.dumps(publish(), indent=2))


def cmd_run_all(args) -> None:
    """Same sequence as the Airflow DAG, for local runs and CI."""
    from sales_pipeline.ingestion.load import load_all
    from sales_pipeline.monitoring.runner import run_all_checks
    from sales_pipeline.publish.tableau import publish
    from sales_pipeline.quality.validate import run_layer
    from sales_pipeline.tiering.pipeline import run

    load_all(full_refresh=args.full_refresh)
    run_layer("raw")
    _dbt("deps")
    _dbt("source", "freshness")
    _dbt("build", "--exclude", "tag:post_tiering")
    run_layer("marts")
    run()
    run_layer("tiering")
    _dbt("build", "--select", "tag:post_tiering")
    run_all_checks()
    publish()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="sales-pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate-data", help="write synthetic source files to DATA_DIR")
    p.add_argument("--customers", type=int, default=1000)
    p.add_argument("--years", type=float, default=3.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--inject-schema-drift", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("load", help="extract + load all sources into the raw schema")
    p.add_argument("--full-refresh", action="store_true")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("validate", help="run Great Expectations suites for a layer")
    p.add_argument("layer", choices=["raw", "marts", "tiering"])
    p.add_argument("--no-fail", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("monitor", help="freshness, volume and schema-drift checks")
    p.add_argument("--no-alert", action="store_true")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("tier", help="score, tier, segment and validate customers")
    p.add_argument("--as-of", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_tier)

    p = sub.add_parser("publish", help="export Tableau extracts and publish if configured")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("run-all", help="run the full pipeline locally")
    p.add_argument("--full-refresh", action="store_true")
    p.set_defaults(func=cmd_run_all)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


if __name__ == "__main__":
    main()
