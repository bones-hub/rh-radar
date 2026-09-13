"""
RH Radar - Price Refresh
data.py only ever sees the NEWEST token profiles on DexScreener - once
a token you've already called ages out of that "latest" feed, its
price stops updating in the database entirely. That silently breaks
two things: milestone alerts (2x/5x/10x) stop firing on older calls,
and any win-rate tracker would be comparing against a stale, hours-old
market cap instead of the real current one.

This script re-fetches CURRENT price data for every token that's ever
been alerted on (from alert_history), regardless of whether it still
counts as "new" - so milestones keep firing correctly over time, and
stats.py always has up-to-date numbers to report on.

Run this as part of the same cycle as data.py (see bot.py).
"""

import sqlite3
from datetime import datetime, timezone

from data import DB_PATH, CHAIN_ID, get_pairs_for_token, pair_age_hours

# Keeps each cycle fast and API-friendly - one request per token. If you
# have more tracked tokens than this, older ones just wait for the next
# cycle; raise this if you want everything refreshed every single time.
MAX_TOKENS_PER_RUN = 60


def get_tracked_token_addresses(conn):
    rows = conn.execute("SELECT DISTINCT token_address FROM alert_history").fetchall()
    return [r[0] for r in rows]


def run_once(conn):
    token_addresses = get_tracked_token_addresses(conn)
    if not token_addresses:
        print("No previously-alerted tokens to refresh yet.")
        return

    collected_at = datetime.now(timezone.utc).isoformat()
    refreshed = 0

    for address in token_addresses[:MAX_TOKENS_PER_RUN]:
        try:
            pairs = get_pairs_for_token(address)
        except Exception as e:
            print(f"  Failed to refresh {address}: {e}")
            continue

        for pair in pairs:
            if pair.get("chainId") != CHAIN_ID:
                continue

            base_token = pair.get("baseToken") or {}
            symbol = base_token.get("symbol", "?")
            liquidity_usd = (pair.get("liquidity") or {}).get("usd")
            volume_h24 = (pair.get("volume") or {}).get("h24")
            price_change_h24 = (pair.get("priceChange") or {}).get("h24")
            created_ms = pair.get("pairCreatedAt")
            age_hrs = pair_age_hours(created_ms)

            conn.execute("""
                INSERT INTO raw_pairs (
                    collected_at, token_address, pair_address, symbol, dex_id,
                    price_usd, liquidity_usd, volume_h24, price_change_h24,
                    fdv, market_cap, pair_created_at, pair_age_hours, dex_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                collected_at, address, pair.get("pairAddress"), symbol, pair.get("dexId"),
                pair.get("priceUsd"), liquidity_usd, volume_h24, price_change_h24,
                pair.get("fdv"), pair.get("marketCap"),
                datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat() if created_ms else None,
                age_hrs, pair.get("url"),
            ))
            refreshed += 1

    conn.commit()
    print(f"Refreshed price data for {refreshed} previously-called token pair row(s).")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    print("RH Radar - Price Refresh")
    run_once(conn)
    conn.close()