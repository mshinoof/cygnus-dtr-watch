"""
Sends the alert. Telegram by default (instant, free, no SMTP headaches),
email as an alternative. Both read credentials from environment variables so
nothing sensitive lands in the repo.

Telegram setup, one time:
    1. Message @BotFather on Telegram, send /newbot, follow the prompts.
       He gives you a token like 8123456789:AAF...
    2. Create a group (e.g. "Cygnus DTR alerts"), add your bot to it.
    3. Send any message in the group, then open:
         https://api.telegram.org/bot<TOKEN>/getUpdates
       and copy the "chat":{"id": -100...} value.
    4. Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

ICON = {
    "DTR_UPGRADED": "\u2b06\ufe0f",
    "NEW_DTR": "\u2728",
    "CAPACITY_FREED": "\u2705",
    "CAPACITY_TAKEN": "\u26a0\ufe0f",
    "DTR_DOWNGRADED": "\u2b07\ufe0f",
    "DTR_REMOVED": "\u274c",
}


def build_message(changes: list[dict], summary: dict, captured_at: str,
                  dashboard_url: str | None = None, max_lines: int = 25) -> str:
    if not changes:
        return ""

    head = f"*KSEB DTR changes* \u2014 {captured_at[:16].replace('T', ' ')} IST"
    net = summary["net_headroom_kw"]
    sign = "+" if net >= 0 else ""
    head += f"\n{summary['total']} change(s), net headroom {sign}{net} kW"

    lines = [head, ""]
    by_section: dict[str, list[dict]] = {}
    for c in changes[:max_lines]:
        by_section.setdefault(f"{c['district']} / {c['section']}", []).append(c)

    for section, items in by_section.items():
        lines.append(f"*{section}*")
        for c in items:
            icon = ICON.get(c["change_type"], "\u2022")
            name = c["transformer"]
            if c["change_type"] == "NEW_DTR":
                lines.append(f"{icon} {name} \u2014 new, {c['new_value']} kW available")
            elif c["change_type"] in ("DTR_UPGRADED", "DTR_DOWNGRADED"):
                lines.append(
                    f"{icon} {name} \u2014 capacity {c['old_value']} \u2192 "
                    f"{c['new_value']} kW (balance now {c['balance_after']} kW)"
                )
            elif c["change_type"] == "DTR_REMOVED":
                lines.append(f"{icon} {name} \u2014 delisted")
            else:
                d = c["balance_delta"]
                lines.append(
                    f"{icon} {name} \u2014 balance {c['balance_before']} \u2192 "
                    f"{c['balance_after']} kW ({'+' if d >= 0 else ''}{d})"
                )
        lines.append("")

    if len(changes) > max_lines:
        lines.append(f"\u2026and {len(changes) - max_lines} more.")
    if dashboard_url:
        lines.append(f"\nFull view: {dashboard_url}")
    return "\n".join(lines).strip()


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("  telegram: not configured, skipping")
        return False
    # Telegram caps a message at 4096 characters.
    for chunk_start in range(0, len(text), 3900):
        payload = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": text[chunk_start:chunk_start + 3900],
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            }
        ).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=payload
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if json.load(r).get("ok") is not True:
                    print("  telegram: rejected the message")
                    return False
        except Exception as e:
            print(f"  telegram: send failed ({e})")
            return False
    print("  telegram: sent")
    return True


def send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("ALERT_EMAIL_TO")
    if not all([host, user, password, to]):
        print("  email: not configured, skipping")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=30) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
        print("  email: sent")
        return True
    except Exception as e:
        print(f"  email: send failed ({e})")
        return False


def dispatch(changes: list[dict], summary: dict, captured_at: str,
             dashboard_url: str | None = None) -> None:
    text = build_message(changes, summary, captured_at, dashboard_url)
    if not text:
        print("  no changes, nothing sent")
        return
    send_telegram(text)
    send_email(
        f"KSEB DTR: {summary['total']} change(s), "
        f"{summary['net_headroom_kw']:+} kW net",
        text.replace("*", ""),
    )
