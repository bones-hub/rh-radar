"""
RH Radar - Lane 3: New Launch (v2 - verified + clone-safe + repeat suppression)
Reads the raw pair data collected by data.py, dedupes each token down to
its single highest-liquidity pair, and filters to genuinely fresh,
tradeable launches. Now brought up to the same standard as the other
two lanes: every candidate is live-verified, checked for clones across
the full batch, and only re-shown if something's actually changed.

Filters (set from real test data on 2026-09-03):
  - Liquidity >= $5,000  (cuts dead/duplicate pools)
  - Pair age < 3 hours   (keeps this lane meaningfully "new")

Run this after data.py has collected at least one batch.
"""

import sqlite3
from datetime import datetime, timezone
from verify import verify_pair_is_real
from common import init_alert_history, get_global_duplicate_symbol_details, should_alert, flag_wash_trading_risk, format_duration
from telegram_alert import send_telegram_alert, format_candidate_message, format_milestone_message

DB_PATH = "/data/rh_radar.db"
LANE = "launches"
MIN_LIQUIDITY = 5000
MAX_AGE_HOURS = 3

QUERY = """
    WITH latest_collection AS (
        SELECT MAX(collected_at) AS ts FROM raw_pairs
    ),
    best_pair_per_token AS (
        SELECT r.*,
               ROW_NUMBER() OVER (
                   PARTITION BY r.token_address
                   ORDER BY r.liquidity_usd DESC
               ) AS rn
        FROM raw_pairs r, latest_collection lc
        WHERE r.collected_at = lc.ts
    )
    SELECT symbol, token_address, pair_address, dex_id, dex_url,
           liquidity_usd, volume_h24, price_change_h24,
           market_cap, pair_age_hours
    FROM best_pair_per_token
    WHERE rn = 1
      AND liquidity_usd >= ?
      AND pair_age_hours IS NOT NULL
      AND pair_age_hours <= ?
    ORDER BY market_cap ASC
"""

if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    init_alert_history(conn)

    latest_ts = conn.execute("SELECT MAX(collected_at) FROM raw_pairs").fetchone()[0]
    raw_candidates = conn.execute(QUERY, (MIN_LIQUIDITY, MAX_AGE_HOURS)).fetchall()

    print("RH Radar - Lane 3: New Launch (verified)")
    print(f"Filters: liquidity >= ${MIN_LIQUIDITY:,}, age <= {MAX_AGE_HOURS}h\n")

    if not raw_candidates:
        print("No candidates passed the filters on this run. "
              "Run data.py again in a bit to collect a fresh batch.")
        conn.close()
        exit()

    duplicate_details = get_global_duplicate_symbol_details(conn, latest_ts)
    duplicate_symbols = set(duplicate_details.keys())
    if duplicate_details:
        print("⚠️  Duplicate symbol(s) found somewhere in this batch - check these before "
              "trusting the exclusion, a shared common ticker isn't automatically a clone:")
        for symbol, addrs in duplicate_details.items():
            print(f"   {symbol}:")
            for addr, mcap, liq in addrs:
                print(f"     {addr} | mcap: ${mcap or 0:,.0f} | liq: ${liq or 0:,.0f}")
        print("   Excluding all of them from results below.\n")

    now_iso = datetime.now(timezone.utc).isoformat()
    verified_results = []
    repeat_skipped = 0

    for row in raw_candidates:
        (symbol, token_address, pair_address, dex_id, dex_url,
         liquidity_usd, volume_h24, price_change_h24,
         market_cap, pair_age_hours) = row

        if symbol in duplicate_symbols:
            continue

        print(f"Verifying {symbol}...", end=" ")
        if not verify_pair_is_real(pair_address):
            print("GHOST - excluded.")
            continue
        print("real.")

        show, is_repeat, times, milestone = should_alert(conn, LANE, symbol, token_address, market_cap, now_iso)
        if not show:
            repeat_skipped += 1
            continue

        verified_results.append((row, is_repeat, times, milestone))

    print()
    if repeat_skipped:
        print(f"({repeat_skipped} already-seen candidate(s) skipped - no significant mcap move since last alert)\n")

    if not verified_results:
        print("No new candidates survived this run.")
    else:
        print(f"{len(verified_results)} candidate(s):\n")
        for row, is_repeat, times, milestone in verified_results:
            (symbol, token_address, pair_address, dex_id, dex_url,
             liquidity_usd, volume_h24, price_change_h24,
             market_cap, pair_age_hours) = row

            risk_flag = " ⚠️ high vol/liq ratio" if flag_wash_trading_risk(liquidity_usd, volume_h24) else ""
            if milestone:
                status = f"(🚀 {milestone['multiple']}x reached in {format_duration(milestone['elapsed_seconds'])})"
            else:
                status = "(NEW)"
            is_priority = market_cap is not None and market_cap <= 25000
            priority_flag = " 🔥" if is_priority else ""

            print(f"{symbol:10s} | liq: ${liquidity_usd:,.0f} | vol24h: ${volume_h24 or 0:,.0f} | "
                  f"mcap: ${market_cap or 0:,.0f} | {pair_age_hours}h old | {dex_id}{risk_flag}{priority_flag} {status}")
            print(f"           VERIFIED LINK: {dex_url}\n")

            if milestone:
                message = format_milestone_message(
                    symbol, milestone["multiple"], milestone["first_mcap"],
                    market_cap, milestone["elapsed_seconds"], dex_url, format_duration
                )
            else:
                extra_line = f"Age: {pair_age_hours}h"
                if risk_flag:
                    extra_line += "\n⚠️ High vol/liq ratio"
                message = format_candidate_message(
                    "New Launch", symbol, liquidity_usd, volume_h24, market_cap,
                    extra_line, dex_url, "NEW",
                    token_address=token_address, priority=is_priority
                )
            send_telegram_alert(message)

    conn.close()