"""
RH Radar - Bot Entry Point
This is what you run (locally or on Railway) - it does two things in
one process:
  1. Listens for /start and /stop so anyone can find the bot on
     Telegram, subscribe, and start getting calls.
  2. Runs the scanning pipeline (data.py -> undervalued_early.py ->
     momentum.py -> launches.py) on a loop in the background, exactly
     like you were doing manually before - each lane script now
     broadcasts its own alerts to every current subscriber.

Needs TELEGRAM_BOT_TOKEN set (in .env locally, or as a Railway
Variable). Does NOT need TELEGRAM_CHAT_ID anymore - subscribers
replace that.
"""

import os
import sqlite3
import subprocess
import asyncio
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from subscribers import init_subscribers, add_subscriber, remove_subscriber, subscriber_count
from stats import build_report

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "rh_radar.db")
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))

# --- TEMPORARY DIAGNOSTIC (safe to remove once we've confirmed the DB path) ---
# Prints the resolved DB path and whether a file already exists there at
# startup, so we can see exactly where data.py/bot.py are reading and
# writing on Railway - doesn't change any behavior, just logs facts.
print(f"[diagnostic] DB_PATH env var = {os.getenv('DB_PATH')!r}")
print(f"[diagnostic] Resolved absolute path = {os.path.abspath(DB_PATH)}")
print(f"[diagnostic] File exists at that path already? {os.path.exists(DB_PATH)}")
if os.path.exists(DB_PATH):
    print(f"[diagnostic] File size = {os.path.getsize(DB_PATH)} bytes")
# --- END TEMPORARY DIAGNOSTIC ---

# data.py discovers brand-new tokens; refresh_prices.py keeps price
# data current for every token ever called, even after it ages out of
# the "newest profiles" feed - both need to run before the lanes check.
SCRIPTS = ["data.py", "refresh_prices.py", "undervalued_early.py", "momentum.py", "launches.py"]

# --- Single-instance lock ---
# Guards against two bot.py processes running at once (e.g. an old
# Railway deployment that never fully stopped, sitting alongside a new
# one). Two active instances would each poll Telegram with the same
# bot token and each run the scan pipeline independently - doubling
# real request volume against DexScreener and producing interleaved,
# confusing logs. Only one instance is allowed to be "active" at a
# time, tracked via a lock row in the same shared database every
# instance already connects to. If the active instance stops sending
# heartbeats (crashed, or is a dead leftover deployment), a waiting
# instance takes over automatically after LOCK_STALE_SECONDS.
INSTANCE_ID = uuid.uuid4().hex[:8]
LOCK_STALE_SECONDS = int(os.getenv("LOCK_STALE_SECONDS", "300"))
LOCK_RETRY_SECONDS = 20


def init_instance_lock(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instance_lock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            instance_id TEXT,
            last_heartbeat TEXT
        )
    """)
    conn.commit()


def try_claim_lock(conn):
    """Returns True if this process now holds the lock - either nobody
    held it yet, we already hold it, or the previous holder's
    heartbeat is stale (crashed / dead leftover deployment)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT instance_id, last_heartbeat FROM instance_lock WHERE id = 1"
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO instance_lock (id, instance_id, last_heartbeat) VALUES (1, ?, ?)",
            (INSTANCE_ID, now_iso),
        )
        conn.commit()
        return True

    holder_id, last_heartbeat = row
    if holder_id == INSTANCE_ID:
        conn.execute("UPDATE instance_lock SET last_heartbeat = ? WHERE id = 1", (now_iso,))
        conn.commit()
        return True

    try:
        age_seconds = (
            datetime.now(timezone.utc) - datetime.fromisoformat(last_heartbeat)
        ).total_seconds()
    except (TypeError, ValueError):
        age_seconds = LOCK_STALE_SECONDS + 1  # unreadable timestamp - safe to take over

    if age_seconds > LOCK_STALE_SECONDS:
        print(f"[lock] Previous holder {holder_id} hasn't checked in for "
              f"{int(age_seconds)}s - taking over.")
        conn.execute(
            "UPDATE instance_lock SET instance_id = ?, last_heartbeat = ? WHERE id = 1",
            (INSTANCE_ID, now_iso),
        )
        conn.commit()
        return True

    return False


def refresh_lock(conn):
    """Call periodically while active, so this instance's claim doesn't
    go stale and get mistakenly taken over mid-run."""
    conn.execute(
        "UPDATE instance_lock SET last_heartbeat = ? WHERE id = 1 AND instance_id = ?",
        (datetime.now(timezone.utc).isoformat(), INSTANCE_ID),
    )
    conn.commit()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    init_subscribers(conn)
    chat_id = update.effective_chat.id
    username = update.effective_user.username or update.effective_user.first_name or "unknown"
    now_iso = datetime.now(timezone.utc).isoformat()
    add_subscriber(conn, chat_id, username, now_iso)
    count = subscriber_count(conn)
    conn.close()

    await update.message.reply_text(
        "You're subscribed to RH Radar. 🎯\n\n"
        "You'll get an alert whenever a new candidate clears the filters "
        "(New Launch, Undervalued Early, or Momentum), plus milestone pings "
        "(2x, 5x, 10x...) as it moves.\n\n"
        "Send /stop anytime to unsubscribe."
    )
    print(f"[bot] {username} ({chat_id}) subscribed - {count} active subscriber(s) total.")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    init_subscribers(conn)
    chat_id = update.effective_chat.id
    remove_subscriber(conn, chat_id)
    conn.close()

    await update.message.reply_text("You've been unsubscribed. Send /start anytime to rejoin.")
    print(f"[bot] {chat_id} unsubscribed.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    report = build_report(conn)
    conn.close()
    await update.message.reply_text(report)


def run_scan_cycle():
    """Runs the full pipeline once, in order, as subprocesses - same as
    running each script manually. Each lane script broadcasts its own
    alerts to subscribers internally, so nothing else is needed here."""
    for script in SCRIPTS:
        try:
            subprocess.run(["python", script], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[bot] {script} exited with an error: {e}")
        except Exception as e:
            print(f"[bot] Failed to run {script}: {e}")


async def scan_loop():
    """Runs forever in the background alongside the bot's command
    listener. Uses a thread so the blocking subprocess calls don't
    freeze /start and /stop from responding."""
    loop = asyncio.get_event_loop()
    while True:
        conn = sqlite3.connect(DB_PATH)
        refresh_lock(conn)
        conn.close()
        await loop.run_in_executor(None, run_scan_cycle)
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


async def post_init(application: Application):
    application.create_task(scan_loop())


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Add it to your .env file locally, "
            "or as a Railway Variable."
        )

    conn = sqlite3.connect(DB_PATH)
    init_subscribers(conn)
    init_instance_lock(conn)

    while not try_claim_lock(conn):
        print(f"[lock] Another instance is currently active - waiting "
              f"{LOCK_RETRY_SECONDS}s before checking again. This instance "
              f"will take over automatically if the other one goes silent "
              f"for {LOCK_STALE_SECONDS}s (e.g. a crashed or leftover deploy).")
        conn.close()
        time.sleep(LOCK_RETRY_SECONDS)
        conn = sqlite3.connect(DB_PATH)

    print(f"[lock] This instance ({INSTANCE_ID}) is now the active RH Radar process.")
    conn.close()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("stats", stats_command))

    print("RH Radar bot is running. Waiting for /start...")
    application.run_polling()


if __name__ == "__main__":
    main()