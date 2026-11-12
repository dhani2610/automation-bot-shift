"""Telegram Bot notification helper for Locum ED HJ Auto-Booking."""

from __future__ import annotations

import html
import warnings

warnings.filterwarnings("ignore", message=".*urllib3.*doesn't match a supported version.*")

import requests


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    timeout: float = 10.0,
) -> tuple[bool, str]:
    """Send an HTML-formatted message via Telegram Bot API."""
    token = (bot_token or "").strip()
    target = (chat_id or "").strip()
    if not token or not target:
        return False, "Bot Token atau Chat ID kosong."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": target,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=timeout,
        )
        data = resp.json()
        if resp.ok and data.get("ok"):
            return True, "Terkirim ke Telegram."
        desc = data.get("description") or f"HTTP {resp.status_code}"
        return False, f"Telegram API error: {desc}"
    except requests.RequestException as exc:
        return False, f"Gagal koneksi ke Telegram: {exc}"
    except ValueError:
        return False, "Respons Telegram tidak valid."


def format_booking_notification(
    *,
    outcome: str,
    account_name: str,
    ic_number: str,
    role: str,
    workplace: str,
    slot_date: str,
    slot_label: str,
    slot_key: str,
    seat: str | None = None,
    booking_ref: str | None = None,
    job_title: str | None = None,
    reason: str | None = None,
) -> str:
    """Build a clear HTML notification message for Telegram."""
    e = html.escape
    if outcome == "SUCCESS":
        header = "✅ <b>BERHASIL DAPAT BOOKING!</b>"
    elif outcome == "DRY_RUN":
        header = "🧪 <b>SIMULASI BOOKING (DRY-RUN)</b>"
    elif outcome == "FAILED":
        header = "❌ <b>GAGAL BOOKING SLOT</b>"
    else:
        header = "⚠️ <b>BELUM DAPAT SLOT BOOKING</b>"

    lines = [
        header,
        "",
        f"👤 <b>Nama:</b> {e(account_name or ic_number)} ({e(role or '-')})",
        f"🏥 <b>Tempat Kerja:</b> {e(workplace or '-')}",
        f"📅 <b>Tanggal:</b> {e(slot_date)}",
        f"⏰ <b>Jam Shift:</b> {e(slot_label or slot_key)} (<code>{e(slot_key)}</code>)",
    ]
    if seat:
        lines.append(f"💺 <b>Seat:</b> {e(seat)}")
    if booking_ref:
        lines.append(f"🔖 <b>Ref ID:</b> <code>{e(booking_ref)}</code>")
    if job_title:
        lines.append(f"🤖 <b>Sumber:</b> {e(job_title)}")
    if reason:
        lines.append(f"📝 <b>Detail:</b> {e(reason)}")
    return "\n".join(lines)

