"""Alert routing for monitoring results and Airflow task failures."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import requests

from sales_pipeline.config import get_settings, pipeline_config
from sales_pipeline.monitoring.results import SEVERITY_ORDER, CheckResult

log = logging.getLogger(__name__)

ICONS = {"warn": ":warning:", "error": ":rotating_light:"}


def format_alert(results: list[CheckResult], title: str = "Sales pipeline data alert") -> str:
    lines = [title]
    for r in sorted(results, key=lambda r: -SEVERITY_ORDER[r.severity]):
        lines.append(f"{ICONS.get(r.severity, '')} [{r.severity.upper()}] {r.check_type} | {r.target}: {r.message}")
    return "\n".join(lines)


def _send_slack(text: str) -> bool:
    url = get_settings().slack_webhook_url
    if not url:
        return False
    resp = requests.post(url, json={"text": text}, timeout=10)
    resp.raise_for_status()
    return True


def _send_email(subject: str, body: str) -> bool:
    s = get_settings()
    if not (s.smtp_host and s.alert_email_to and s.alert_email_from):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.alert_email_from
    msg["To"] = s.alert_email_to
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password or "")
        smtp.send_message(msg)
    return True


def send_message(subject: str, body: str, channels: list[str] | None = None) -> list[str]:
    channels = channels or pipeline_config()["alerting"]["channels"]
    delivered = []
    for channel in channels:
        try:
            if channel == "log":
                log.warning("%s\n%s", subject, body)
                delivered.append("log")
            elif channel == "slack" and _send_slack(f"*{subject}*\n{body}"):
                delivered.append("slack")
            elif channel == "email" and _send_email(subject, body):
                delivered.append("email")
        except Exception:  # alerting must never take the pipeline down
            log.exception("Failed to deliver alert via %s", channel)
    return delivered


def send_alerts(results: list[CheckResult], min_severity: str | None = None) -> list[CheckResult]:
    """Alert on results at or above ``min_severity``. Returns the alerted results."""
    min_severity = min_severity or pipeline_config()["alerting"]["min_severity"]
    threshold = SEVERITY_ORDER[min_severity]
    to_alert = [r for r in results if SEVERITY_ORDER[r.severity] >= threshold and r.severity != "pass"]
    if to_alert:
        worst = "ERROR" if any(r.failed for r in to_alert) else "WARN"
        send_message(f"[{worst}] Sales pipeline: {len(to_alert)} check(s) need attention", format_alert(to_alert))
    return to_alert


def airflow_failure_callback(context) -> None:
    """``on_failure_callback`` for Airflow tasks."""
    ti = context.get("task_instance")
    dag_id = getattr(ti, "dag_id", "?")
    task_id = getattr(ti, "task_id", "?")
    body = (
        f"DAG: {dag_id}\nTask: {task_id}\nRun: {context.get('run_id')}\n"
        f"Try: {getattr(ti, 'try_number', '?')}\nError: {context.get('exception')}\n"
        f"Log: {getattr(ti, 'log_url', '')}"
    )
    send_message(f"[ERROR] Airflow task failed: {dag_id}.{task_id}", body)


def airflow_sla_miss_callback(dag, task_list, blocking_task_list, slas, blocking_tis) -> None:
    send_message(f"[WARN] SLA missed in {dag.dag_id}", f"Tasks: {task_list}\nBlocking: {blocking_task_list}")
