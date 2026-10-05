"""Email digest of the jobs first seen today, sent through Gmail SMTP."""

import html
import logging
import os
import smtplib
from datetime import date
from email.message import EmailMessage

log = logging.getLogger("get-hired")

SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
LEVEL_ORDER = ["intern", "graduate", "junior", "mid", "senior"]


def _salary(job: dict) -> str:
    """ "GBP 45,000 to 60,000", or "" when the listing gave no figure."""
    low, high = job.get("salary_min"), job.get("salary_max")
    numbers = [n for n in (low, high) if isinstance(n, (int, float))]
    if not numbers:
        return ""
    currency = f"{job.get('salary_currency')} " if job.get("salary_currency") else ""
    if len(numbers) == 2:
        return f"{currency}{low:,.0f}" if low == high else f"{currency}{low:,.0f} to {high:,.0f}"
    return f"{currency}{'from' if isinstance(low, (int, float)) else 'up to'} {numbers[0]:,.0f}"


def group_by_level(jobs: list[dict]) -> list[tuple[str, list[dict]]]:
    """Levels in career order, then any other level names, each sorted by company and title."""
    levels = LEVEL_ORDER + sorted({job.get("level", "") for job in jobs} - set(LEVEL_ORDER))
    groups = []
    for level in levels:
        members = [job for job in jobs if job.get("level", "") == level]
        if members:
            members.sort(key=lambda job: (job["company"].lower(), job["title"].lower()))
            groups.append((level or "unknown", members))
    return groups


def build_digest(jobs: list[dict], today: date) -> tuple[str, str, str]:
    """Return (subject, plain text body, HTML body)."""
    count = len(jobs)
    subject = f"Get Hired: {count} new job{'' if count == 1 else 's'} ({today.isoformat()})"
    text, page = [], []
    for level, members in group_by_level(jobs):
        heading = f"{level.capitalize()} ({len(members)})"
        text += [heading, "-" * len(heading)]
        page += [f"<h3>{html.escape(heading)}</h3>", "<ul>"]
        for job in members:
            details = " | ".join(part for part in (job.get("location", ""), _salary(job)) if part)
            text += [f"{job['company']}: {job['title']}", *([f"  {details}"] if details else []), f"  {job['url']}", ""]
            page.append(
                f'<li><b>{html.escape(job["company"])}</b>: '
                f'<a href="{html.escape(job["url"], quote=True)}">{html.escape(job["title"])}</a>'
                + (f"<br>{html.escape(details)}" if details else "")
                + "</li>"
            )
        page.append("</ul>")
    return subject, "\n".join(text).rstrip() + "\n", "\n".join(page)


def send_digest(jobs: list[dict], today: date) -> bool:
    """Email the digest. Returns True if an email was sent.

    Nothing is sent when there are no jobs, or when GMAIL_ADDRESS and
    GMAIL_APP_PASSWORD are not both set. Raises RuntimeError if Gmail refuses.
    """
    if not jobs:
        log.info("Digest: no new jobs today, no email sent")
        return False
    sender, password = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    if not (sender and password):
        log.info("Digest: skipped, GMAIL_ADDRESS and GMAIL_APP_PASSWORD are not both set")
        return False

    subject, text, page = build_digest(jobs, today)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = os.environ.get("DIGEST_TO") or sender
    message.set_content(text)
    message.add_alternative(page, subtype="html")
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            # Google shows app passwords in groups of four; the spaces are not part of it.
            smtp.login(sender, password.replace(" ", ""))
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # Only the error type is reported, so the password can never reach the logs.
        raise RuntimeError(f"could not send the digest ({type(exc).__name__})") from None
    log.info("Digest: emailed %d new jobs", len(jobs))
    return True
