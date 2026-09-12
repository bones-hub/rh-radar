"""
RH Radar - Telegram Alerts
Sends candidate alerts to a Telegram chat/channel via a bot. Needs
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID set in your .env file - see
the setup steps if you don't have these yet.

Fails safely: if credentials are missing or the request fails, this
prints a warning and returns False instead of crashing the scan. A
broken alert should never take down the actual scanning pipeline.

Two message types:
  - format_candidate_message: full stat block, sent once when a token
    is first seen (NEW).
  - format_milestone_message: short one-liner, sent every time a
    already-tracked token crosses a new multiple of its first-seen
    mcap (2x, 3x, 5x, 10x, etc). Keeps the channel from being spammed
    with the full block every time - only the first call gets the
    detailed layout, everything after that is a quick "X reached" ping.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Small-caps character mapping for stylized Telegram text
SMALL_CAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘqʀꜱᴛᴜᴠᴡxʏᴢ"
)

# Anything at or below this mcap gets the 🔥 priority flag in alerts.
# This is the "main reason the bot exists" threshold - keep it in sync
# with what you actually consider "the good stuff."
PRIORITY_MCAP_THRESHOLD = 25000


def to_small_caps(text: str) -> str:
    """Convert lowercase text to small-caps unicode for Telegram display."""
    return text.lower().translate(SMALL_CAPS_MAP)


def format_compact_number(n):
    """34900 -> '34.9K', 2100000 -> '2.1M', 800 -> '$800'. Matches the
    compact style used by most call-channel bots."""
    n = n or 0
    if n >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n/1_000:.1f}K"
    return f"${n:,.0f}"


def send_telegram_alert(text, reply_to_message_id=None):
    """Sends the alert. Returns the Telegram message_id on success (so
    later milestone alerts can reply/quote it), or None on failure."""
    if not BOT_TOKEN or not CHAT_ID:
        print("  (Telegram not configured - skipping alert. Set TELEGRAM_BOT_TOKEN "
              "and TELEGRAM_CHAT_ID in your .env file.)")
        return None

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True

    try:
        resp = requests.post(SEND_URL.format(token=BOT_TOKEN), data=payload, timeout=10)
        if resp.status_code != 200:
            print(f"  (Telegram alert failed: {resp.status_code} {resp.text[:200]})")
            return None
        return resp.json().get("result", {}).get("message_id")
    except requests.RequestException as e:
        print(f"  (Telegram alert failed: {e})")
        return None


def format_candidate_message(lane_label, symbol, liquidity_usd, volume_h24,
                              market_cap, extra_line, dex_url, status_label,
                              token_address=None, priority=None):
    """
    Full stat-block message, sent when a token is FIRST seen on a lane.
    Tree-style layout (├ / └) with small-caps labels, values left plain
    for readability. extra_line is lane-specific, e.g. "Age: 0.5h" or
    "24h Change: +131.0%" - split on the first colon into its own row.
    """
    if priority is None:
        priority = bool(market_cap is not None and market_cap <= PRIORITY_MCAP_THRESHOLD)

    lane_sc = to_small_caps(lane_label.replace("_", " "))
    fire = "🔥 " if priority else ""

    if ":" in extra_line:
        extra_label, extra_value = extra_line.split(":", 1)
        extra_label_sc = to_small_caps(extra_label.strip())
        extra_value = extra_value.strip()
    else:
        extra_label_sc = to_small_caps("note")
        extra_value = extra_line

    addr_line = f"<code>{token_address}</code>\n" if token_address else ""

    return (
        f"🆕 {fire}{to_small_caps('new call')} · <b>{lane_sc}</b>\n\n"
        f"<b>${symbol.upper()}</b>\n"
        f"{addr_line}\n"
        f"📊 {to_small_caps('stats')}\n"
        f"├ {to_small_caps('liquidity')}   {format_compact_number(liquidity_usd)}\n"
        f"├ {to_small_caps('volume 24h')}   {format_compact_number(volume_h24)}\n"
        f"├ {to_small_caps('market cap')}   {format_compact_number(market_cap)}\n"
        f"└ {extra_label_sc}   {extra_value}\n\n"
        f'🔗 <a href="{dex_url}">{to_small_caps("view chart")}</a>'
    )


def format_milestone_message(symbol, multiple, first_mcap, current_mcap,
                              elapsed_seconds, dex_url, format_duration_fn):
    """
    Short one-line follow-up sent when an already-tracked token crosses
    a new milestone multiple (2x, 3x, 5x, ...). Deliberately terse -
    no stat block, no repeat of info already sent in the NEW message.
    Mirrors the "🚀 $MAPLE 60.1x 💹 34.9K ↗️ 2.1M in 1h:10m" style.
    """
    duration_str = format_duration_fn(elapsed_seconds)
    return (
        f"🚀 <b>${symbol.upper()}</b> {multiple}x reached\n"
        f"💹 {format_compact_number(first_mcap)} ↗️ {format_compact_number(current_mcap)} "
        f"in {duration_str}\n"
        f'🔗 <a href="{dex_url}">{to_small_caps("view chart")}</a>'
    )