"""
RH Radar - Lane 2: Momentum (v3 - global clone check + repeat suppression)
Tokens above $100k mcap showing REAL momentum toward $1M. Every candidate is:
  1. Live-verified against DexScreener's own API right before display
  2. Checked for duplicate symbols across the ENTIRE latest batch, not just
     this lane's filtered results (catches a clone sitting in a different
     mcap band than the real token)
  3. Capped at a sane max 24h % change - absurd swings are usually noise
     from a near-empty starting pool, not real momentum
  4. Only re-shown if its mcap has moved meaningfully since the last time
     you saw it - no more identical repeats clogging every scan
"""

import os
import sqlite3
from datetime import datetime, timezone
from verify import verify_pair_is_real
from common import init_alert_history, get_global_duplicate_symbol_details, should_alert, flag_wash_trading_risk, format_duration
from telegram_alert import broadcast_new_alert, broadcast_milestone_alert, format_candidate_message, format_milestone_message
from subscribers import init_subscribers

DB_PATH = os.getenv("DB_PATH", "rh_radar.db")
LANE = "momentum"
MIN_MCAP = 100000
MAX_MCAP = 1000000
MIN_LIQUIDITY = 10000
MIN_VOL_TO_MCAP = 0.15
MAX_SANE_PRICE_CHANGE = 500  # exclude anything above +500% in 24h as likely noise

QUERY = """
    -- Each token uses its OWN most recent row, not a single global
    -- "latest collected_at" match - see launches.py for the full
    -- explanation of why the old version silently dropped fresh
    -- tokens.
    WITH best_pair_per_token AS (
        SELECT r.*,
               ROW_NUMBER() OVER (
                   PARTITION BY r.token_address
                   ORDER BY r.collected_at DESC, r.liquidity_usd DESC
               ) AS rn
        FROM raw_pairs r
    )
    SELECT symbol, token_address, pair_address, dex_id, dex_url,
           liquidity_usd, volume_h24, price_change_h24,
           market_cap, pair_age_hours
    FROM best_pair_per_token
    WHERE rn = 1
      AND liquidity_usd >= ?
      AND market_cap BETWEEN ? AND ?
      AND price_change_h24 > 0
      AND price_change_h24 <= ?
      AND volume_h24 >= (market_cap * ?)
    ORDER BY price_change_h24 DESC
"""

if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    init_alert_history(conn)
    init_subscribers(conn)

    latest_ts = conn.execute("SELECT MAX(collected_at) FROM raw_pairs").fetchone()[0]
    raw_candidates = conn.execute(
        QUERY, (MIN_LIQUIDITY, MIN_MCAP, MAX_MCAP, MAX_SANE_PRICE_CHANGE, MIN_VOL_TO_MCAP)
    ).fetchall()

    print("RH Radar - Lane 2: Momentum (verified)")
    print(f"Filters: mcap ${MIN_MCAP:,}-${MAX_MCAP:,}, liquidity >= ${MIN_LIQUIDITY:,}, "
          f"0-{MAX_SANE_PRICE_CHANGE}% 24h change, volume >= {MIN_VOL_TO_MCAP*100:.0f}% of mcap\n")

    if not raw_candidates:
        print("No candidates passed the filters on this run. "
              "That's expected most of the time - we want few and real, not frequent.")
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

            risk_flag = " ⚠️ high vol/liq ratio - verify carefully" if flag_wash_trading_risk(liquidity_usd, volume_h24) else ""
            if milestone:
                status = f"(🚀 {milestone['multiple']}x reached in {format_duration(milestone['elapsed_seconds'])})"
            else:
                status = "(NEW)"

            print(f"{symbol:10s} | liq: ${liquidity_usd:,.0f} | vol24h: ${volume_h24 or 0:,.0f} | "
                  f"mcap: ${market_cap or 0:,.0f} | 24h: {price_change_h24:+.1f}% | {dex_id}{risk_flag} {status}")
            print(f"           VERIFIED LINK: {dex_url}\n")

            if milestone:
                message = format_milestone_message(
                    symbol, milestone["multiple"], milestone["first_mcap"],
                    market_cap, milestone["elapsed_seconds"], dex_url, format_duration
                )
            else:
                extra_line = f"24h Change: {price_change_h24:+.1f}%"
                if risk_flag:
                    extra_line += "\n⚠️ High vol/liq ratio - verify carefully"
                message = format_candidate_message(
                    "Momentum", symbol, liquidity_usd, volume_h24, market_cap,
                    extra_line, dex_url, "NEW",
                    token_address=token_address
                )
            if milestone:
                broadcast_milestone_alert(conn, token_address, message)
            else:
                broadcast_new_alert(conn, token_address, message)

    conn.close()