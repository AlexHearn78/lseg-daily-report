#!/usr/bin/env python3
"""Email the MDU daily report via SMTP (Gmail-compatible).

Sends an email-safe HTML summary inline plus the full interactive report
as an attachment. Configuration via environment variables:

    SMTP_USER            sender address, e.g. you@gmail.com
    SMTP_APP_PASSWORD    Gmail app password (never your account password)
    SMTP_TO              comma-separated recipients
    SMTP_HOST            default: smtp.gmail.com
    SMTP_PORT            default: 587 (STARTTLS); 465 selects SMTP_SSL

Usage:
    uv run workflows/mdu_email.py [--date 2026-08-16]
"""
from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

import datetime as dt

from mdu_report import REPORT_TITLE, _short_date, gen_email_html, generate_theme, load_run

logger = logging.getLogger("mdu_email")

REPORT_DIR = Path("data/reports/Daily EQ Research")


def latest_run_date() -> str:
    """Return the most recent run dir name (YYYYMMDD) in data/outputs/mdu/."""
    run_dirs = sorted(
        d for d in Path("data/outputs/mdu").iterdir()
        if d.is_dir() and d.name.isdigit()
    )
    if not run_dirs:
        raise FileNotFoundError("No MDU run directories found in data/outputs/mdu/")
    return run_dirs[-1].name


def _report_path(date_raw: str) -> tuple[Path, str]:
    """Locate the full interactive report; returns (path, display_date)."""
    display = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
    path = REPORT_DIR / f"{display}.html"
    if not path.is_file():
        path = REPORT_DIR / "latest.html"
    if not path.is_file():
        raise FileNotFoundError(f"No report found in {REPORT_DIR} for {display}")
    return path, display


def build_message(date_raw: str, smtp_user: str, recipients: list[str]) -> tuple[MIMEMultipart, str]:
    """Build the MIME message; returns (message, subject)."""
    report_path, display = _report_path(date_raw)
    run = load_run(date_raw)

    n_total = len(run.tickers)
    n_success = sum(1 for t in run.tickers.values() if not t.failed)
    n_fail = n_total - n_success

    status = f"{n_success}/{n_total} OK"
    if n_fail:
        status += f", {n_fail} failed"
    title = f"{REPORT_TITLE} · {_short_date(dt.date.fromisoformat(display))}"
    subject = f"{title} — {status}"

    summary_html = gen_email_html(run)
    theme = generate_theme(run.tickers)
    plain_text = (
        f"{title}\n"
        f"Status: {status}\n"
        f"Theme: {theme}\n"
        f"The full interactive report is attached (daily-report-{display}.html).\n"
    )

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_text, "plain", "utf-8"))
    alt.attach(MIMEText(summary_html, "html", "utf-8"))
    msg.attach(alt)

    attachment = MIMEApplication(report_path.read_text().encode("utf-8"), _subtype="html")
    attachment.add_header("Content-Disposition", "attachment",
                          filename=f"daily-report-{display}.html")
    msg.attach(attachment)

    return msg, subject


def send(smtp_user: str, app_password: str, recipients: list[str],
         msg: MIMEMultipart, host: str, port: int) -> None:
    """Send *msg* via SMTP, STARTTLS on 587 or implicit TLS on 465."""
    server = smtplib.SMTP_SSL(host, port, timeout=60) if port == 465 \
        else smtplib.SMTP(host, port, timeout=60)
    try:
        if port != 465:
            server.ehlo()
            server.starttls()
            server.ehlo()
        server.login(smtp_user, app_password)
        server.sendmail(smtp_user, recipients, msg.as_string())
    finally:
        server.quit()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    p = argparse.ArgumentParser(description="Email the MDU daily report via SMTP")
    p.add_argument("--date", type=str, default=None,
                   help="Run date (YYYY-MM-DD or YYYYMMDD, default: latest)")
    args = p.parse_args()

    date_raw = args.date.replace("-", "") if args.date else latest_run_date()

    smtp_user = os.environ.get("SMTP_USER", "")
    app_password = os.environ.get("SMTP_APP_PASSWORD", "")
    to_raw = os.environ.get("SMTP_TO", "")
    if not smtp_user or not app_password or not to_raw:
        logger.error("SMTP_USER, SMTP_APP_PASSWORD and SMTP_TO must be set")
        return 2
    recipients = [r.strip() for r in to_raw.split(",") if r.strip()]
    if not recipients:
        logger.error("SMTP_TO contains no valid recipients")
        return 2

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        logger.error("SMTP_PORT must be an integer, got %r", os.environ.get("SMTP_PORT"))
        return 2

    try:
        msg, subject = build_message(date_raw, smtp_user, recipients)
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    try:
        send(smtp_user, app_password, recipients, msg, host, port)
    except smtplib.SMTPAuthenticationError as e:
        logger.error("SMTP auth failed for %s (check SMTP_APP_PASSWORD): %s",
                     smtp_user, e)
        return 1
    except Exception as e:
        logger.exception("Failed to send report email: %s", e)
        return 1

    logger.info("Sent: %s -> %s (host=%s:%d)", subject, ", ".join(recipients), host, port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
