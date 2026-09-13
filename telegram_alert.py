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
import concurrent.futures
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Optional second destination - set these two if you want alerts mirrored
# to a completely separate bot/chat as well. Leave unset and this is a
# no-op; nothing breaks if only the primary bot is configured.
BOT_TOKEN_2 = os.getenv("TELEGRAM_BOT_TOKEN_2")
CHAT_ID_2 = os.getenv("TELEGRAM_CHAT_ID_2")

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


def _send_to_destination(token, chat_id, text, reply_to_message_id=None):
    """Low-level single-destination send. Returns message_id or None."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True

    try:
        resp = requests.post(SEND_URL.format(token=token), data=payload, timeout=15)
        if resp.status_code != 200:
            print(f"  (Telegram alert failed: {resp.status_code} {resp.text[:200]})")
            return None
        return resp.json().get("result", {}).get("message_id")
    except requests.RequestException as e:
        print(f"  (Telegram alert failed: {e})")
        return None


def send_telegram_alert(text, reply_to_message_id=None):
    """
    Sends the alert to the primary bot/chat, and also mirrors it to a
    second bot/chat if TELEGRAM_BOT_TOKEN_2 / TELEGRAM_CHAT_ID_2 are set.

    reply_to_message_id only threads correctly on the PRIMARY destination
    (message IDs aren't shared across separate bots/chats) - the second
    destination will just receive it as a normal standalone message,
    which is fine since it's a mirror, not the tracked source of truth.

    Returns the primary destination's message_id (used for milestone
    reply-threading), or None if the primary bot isn't configured or the
    send failed.
    """
    if not BOT_TOKEN or not CHAT_ID:
        print("  (Telegram not configured - skipping alert. Set TELEGRAM_BOT_TOKEN "
              "and TELEGRAM_CHAT_ID in your .env file.)")
        return None

    primary_message_id = _send_to_destination(BOT_TOKEN, CHAT_ID, text, reply_to_message_id)

    if BOT_TOKEN_2 and CHAT_ID_2:
        _send_to_destination(BOT_TOKEN_2, CHAT_ID_2, text)

    return primary_message_id


def broadcast_new_alert(conn, token_address, text):
    """
    Sends a brand-new candidate alert to EVERY active subscriber AT THE
    SAME TIME - all requests fire concurrently instead of looping one
    person at a time, so subscriber #1 and subscriber #50 get it within
    the same moment instead of #50 waiting on everyone ahead of them.
    Each subscriber's own message_id is stored so a later milestone ping
    can reply directly to their copy.
    """
    from subscribers import get_active_subscribers, record_subscriber_message

    if not BOT_TOKEN:
        print("  (Telegram not configured - skipping broadcast. Set TELEGRAM_BOT_TOKEN "
              "in your .env file.)")
        return

    chat_ids = get_active_subscribers(conn)
    if not chat_ids:
        print("  (No subscribers yet - nothing to broadcast to. Have someone /start the bot.)")
        return

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(30, len(chat_ids))) as pool:
        future_to_chat = {
            pool.submit(_send_to_destination, BOT_TOKEN, chat_id, text): chat_id
            for chat_id in chat_ids
        }
        for future in concurrent.futures.as_completed(future_to_chat):
            chat_id = future_to_chat[future]
            try:
                results[chat_id] = future.result()
            except Exception as e:
                print(f"  (Broadcast to {chat_id} failed: {e})")
                results[chat_id] = None

    sent = 0
    for chat_id, msg_id in results.items():
        if msg_id is not None:
            record_subscriber_message(conn, token_address, chat_id, msg_id)
            sent += 1
    print(f"  (Broadcast to {sent}/{len(chat_ids)} subscriber(s), sent simultaneously.)")


def broadcast_milestone_alert(conn, token_address, text):
    """
    Sends a milestone follow-up to every active subscriber AT THE SAME
    TIME, replying to each person's OWN copy of the original NEW alert
    where we have it on record. If someone subscribed after the
    original alert went out, they just get it as a standalone message
    instead of a reply.
    """
    from subscribers import get_active_subscribers, get_subscriber_message_id

    if not BOT_TOKEN:
        print("  (Telegram not configured - skipping broadcast.)")
        return

    chat_ids = get_active_subscribers(conn)
    if not chat_ids:
        return

    # Reply-target lookups are just local DB reads - cheap, do these
    # first so every concurrent send already knows its own reply target.
    reply_targets = {
        chat_id: get_subscriber_message_id(conn, token_address, chat_id)
        for chat_id in chat_ids
    }

    sent = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(30, len(chat_ids))) as pool:
        future_to_chat = {
            pool.submit(_send_to_destination, BOT_TOKEN, chat_id, text, reply_targets[chat_id]): chat_id
            for chat_id in chat_ids
        }
        for future in concurrent.futures.as_completed(future_to_chat):
            chat_id = future_to_chat[future]
            try:
                if future.result() is not None:
                    sent += 1
            except Exception as e:
                print(f"  (Milestone broadcast to {chat_id} failed: {e})")

    print(f"  (Milestone broadcast to {sent}/{len(chat_ids)} subscriber(s), sent simultaneously.)")


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