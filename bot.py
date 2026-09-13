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
    conn.close()

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("stats", stats_command))

    print("RH Radar bot is running. Waiting for /start...")
    application.run_polling()


if __name__ == "__main__":
    main()