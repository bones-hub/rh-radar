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

v2 changes (fixing a rate-limit problem that was silently breaking
verify.py for REAL candidates too):
  - Tokens that keep failing (dead pair, delisted, permanently 404/429)
    are tracked in a refresh_failures table and skipped after
    MAX_CONSECUTIVE_FAILS in a row, instead of being retried every
    single cycle forever. They get retried again after a cooldown
    period, in case the pair comes back.
  - A small delay between each request spreads the batch out instead
    of firing everything back-to-back, which is what was tripping
    DexScreener's rate limit and causing 429s.
  - MAX_TOKENS_PER_RUN lowered as a second lever on request volume -
    raise it back up once you've confirmed 429s are gone.

Run this as part of the same cycle as data.py (see bot.py).
"""

import time
import sqlite3
from datetime import datetime, timezone, timedelta

import requests
from data import DB_PATH, CHAIN_ID, get_pairs_for_token, pair_age_hours

# Keeps each cycle fast and API-friendly - one request per token.
MAX_TOKENS_PER_RUN = 30

# Small pause between each request so a batch of 30 doesn't fire as one
# burst. 0.3s * 30 tokens = ~9s added to the cycle - cheap insurance
# against tripping DexScreener's rate limit.
REQUEST_DELAY_SECONDS = 0.3

# After this many consecutive failures, stop retrying a token every
# cycle - it's very likely dead (delisted, ghost address, etc).
MAX_CONSECUTIVE_FAILS = 5

# Even a "dead" token gets one retry attempt after this long, in case
# it was a temporary API issue rather than a genuinely gone pair.
RETRY_COOLDOWN_HOURS = 6


def init_refresh_failures(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS refresh_failures (
            token_address TEXT PRIMARY KEY,
            fail_count INTEGER DEFAULT 0,
            last_attempt_at TEXT
        )
    """)
    conn.commit()


def get_tracked_token_addresses(conn):
    rows = conn.execute("SELECT DISTINCT token_address FROM alert_history").fetchall()
    return [r[0] for r in rows]


def get_skippable_tokens(conn, now):
    """Tokens that have failed MAX_CONSECUTIVE_FAILS+ times AND haven't
    hit their retry cooldown yet - these get skipped this run."""
    rows = conn.execute("""
        SELECT token_address, fail_count, last_attempt_at FROM refresh_failures
        WHERE fail_count >= ?
    """, (MAX_CONSECUTIVE_FAILS,)).fetchall()

    skip = set()
    for token_address, fail_count, last_attempt_at in rows:
        try:
            last_attempt = datetime.fromisoformat(last_attempt_at)
        except (TypeError, ValueError):
            continue
        if now - last_attempt < timedelta(hours=RETRY_COOLDOWN_HOURS):
            skip.add(token_address)
    return skip


def record_success(conn, token_address, now_iso):
    conn.execute("""
        INSERT INTO refresh_failures (token_address, fail_count, last_attempt_at)
        VALUES (?, 0, ?)
        ON CONFLICT(token_address) DO UPDATE SET fail_count = 0, last_attempt_at = excluded.last_attempt_at
    """, (token_address, now_iso))
    conn.commit()


def record_failure(conn, token_address, now_iso):
    conn.execute("""
        INSERT INTO refresh_failures (token_address, fail_count, last_attempt_at)
        VALUES (?, 1, ?)
        ON CONFLICT(token_address) DO UPDATE SET
            fail_count = fail_count + 1,
            last_attempt_at = excluded.last_attempt_at
    """, (token_address, now_iso))
    conn.commit()


def fetch_with_backoff(address, retries=1, backoff_seconds=1.5):
    """
    One extra attempt on a 429 specifically, with a short pause -
    a transient rate-limit hit shouldn't count the same as a genuinely
    dead token. Anything else (404, connection error, etc) just raises
    through to the caller immediately.
    """
    for attempt in range(retries + 1):
        try:
            return get_pairs_for_token(address)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 429 and attempt < retries:
                time.sleep(backoff_seconds)
                continue
            raise


def run_once(conn):
    init_refresh_failures(conn)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    all_tracked = get_tracked_token_addresses(conn)
    if not all_tracked:
        print("No previously-alerted tokens to refresh yet.")
        return

    skip_set = get_skippable_tokens(conn, now)
    candidates = [a for a in all_tracked if a not in skip_set]

    refreshed = 0
    newly_failed = 0
    to_process = candidates[:MAX_TOKENS_PER_RUN]

    for i, address in enumerate(to_process):
        try:
            pairs = fetch_with_backoff(address)
        except Exception as e:
            print(f"  Failed to refresh {address}: {e}")
            record_failure(conn, address, now_iso)
            newly_failed += 1
            if i < len(to_process) - 1:
                time.sleep(REQUEST_DELAY_SECONDS)
            continue

        matched_any = False
        for pair in pairs:
            if pair.get("chainId") != CHAIN_ID:
                continue
            matched_any = True

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
                now_iso, address, pair.get("pairAddress"), symbol, pair.get("dexId"),
                pair.get("priceUsd"), liquidity_usd, volume_h24, price_change_h24,
                pair.get("fdv"), pair.get("marketCap"),
                datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat() if created_ms else None,
                age_hrs, pair.get("url"),
            ))
            refreshed += 1

        if matched_any:
            record_success(conn, address, now_iso)
        else:
            # Request succeeded but returned no matching pair - the
            # pair is genuinely gone, not just rate-limited.
            record_failure(conn, address, now_iso)
            newly_failed += 1

        if i < len(to_process) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    conn.commit()

    skipped_count = len(all_tracked) - len(candidates)
    overflow_count = max(0, len(candidates) - MAX_TOKENS_PER_RUN)

    print(f"Refreshed price data for {refreshed} previously-called token pair row(s).")
    if newly_failed:
        print(f"  ({newly_failed} token(s) failed or returned no pair this run.)")
    if skipped_count:
        print(f"  ({skipped_count} token(s) skipped - marked dead after "
              f"{MAX_CONSECUTIVE_FAILS}+ failures, will retry after {RETRY_COOLDOWN_HOURS}h.)")
    if overflow_count:
        print(f"  ({overflow_count} token(s) not reached this run - over the "
              f"{MAX_TOKENS_PER_RUN}/cycle cap, they'll get picked up next cycle.)")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    print("RH Radar - Price Refresh")
    run_once(conn)
    conn.close()