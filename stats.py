"""
RH Radar - Win-Rate Tracker
Reads everything the bot has ever alerted on (alert_history) plus each
token's most recently known market cap (raw_pairs, kept fresh by
refresh_prices.py) and builds a plain-language report: how many calls
hit which milestone, broken down by lane - so you can see which lane
is actually worth trusting, backed by numbers instead of gut feel.

Usable two ways:
  - Standalone: `python stats.py` prints the report to your terminal.
  - Via the bot: /stats in Telegram calls build_report() and replies
    with the same report to whoever asked.
"""

import os
import sqlite3


def _latest_known_mcap(conn, token_address):
    row = conn.execute("""
        SELECT market_cap FROM raw_pairs
        WHERE token_address = ? AND market_cap IS NOT NULL
        ORDER BY collected_at DESC LIMIT 1
    """, (token_address,)).fetchone()
    return row[0] if row else None


def build_report(conn):
    calls = conn.execute("""
        SELECT token_address, lane, symbol, first_alert_at, first_alert_mcap
        FROM alert_history
        ORDER BY first_alert_at ASC
    """).fetchall()

    if not calls:
        return "No calls tracked yet - once the bot alerts on something, stats will show up here."

    total = len(calls)
    by_lane = {}
    bucket_counts = {"10x+": 0, "5x+": 0, "2x+": 0, "flat": 0, "down": 0, "unknown": 0}
    best = None  # (multiple, symbol, lane)
    multiples = []

    for token_address, lane, symbol, first_alert_at, first_alert_mcap in calls:
        lane_stats = by_lane.setdefault(lane, {
            "total": 0, "10x+": 0, "5x+": 0, "2x+": 0, "flat": 0, "down": 0, "unknown": 0
        })
        lane_stats["total"] += 1

        current_mcap = _latest_known_mcap(conn, token_address)
        if not first_alert_mcap or not current_mcap:
            bucket_counts["unknown"] += 1
            lane_stats["unknown"] += 1
            continue

        multiple = current_mcap / first_alert_mcap
        multiples.append(multiple)

        if best is None or multiple > best[0]:
            best = (multiple, symbol, lane)

        if multiple >= 10:
            bucket = "10x+"
        elif multiple >= 5:
            bucket = "5x+"
        elif multiple >= 2:
            bucket = "2x+"
        elif multiple >= 0.5:
            bucket = "flat"
        else:
            bucket = "down"

        bucket_counts[bucket] += 1
        lane_stats[bucket] += 1

    avg_multiple = sum(multiples) / len(multiples) if multiples else 0
    win_count = bucket_counts["2x+"] + bucket_counts["5x+"] + bucket_counts["10x+"]
    win_rate = (win_count / total * 100) if total else 0

    lines = [f"📊 RH Radar Stats - {total} call(s) tracked\n"]
    lines.append(f"Win rate (hit 2x or more): {win_rate:.0f}%")
    lines.append(f"Average multiple: {avg_multiple:.2f}x")
    if best:
        lines.append(f"Best call: ${best[1].upper()} ({best[2]}) - {best[0]:.1f}x")
    lines.append("")
    lines.append(f"🚀 10x+: {bucket_counts['10x+']}")
    lines.append(f"🔥 5x-10x: {bucket_counts['5x+']}")
    lines.append(f"✅ 2x-5x: {bucket_counts['2x+']}")
    lines.append(f"➖ Flat (0.5x-2x): {bucket_counts['flat']}")
    lines.append(f"💀 Down (<0.5x): {bucket_counts['down']}")
    if bucket_counts["unknown"]:
        lines.append(f"❔ No recent price data: {bucket_counts['unknown']}")
    lines.append("")
    lines.append("By lane:")
    for lane, s in by_lane.items():
        lane_wins = s["2x+"] + s["5x+"] + s["10x+"]
        lane_win_rate = (lane_wins / s["total"] * 100) if s["total"] else 0
        lines.append(f"  {lane}: {s['total']} call(s), {lane_win_rate:.0f}% hit 2x+")

    return "\n".join(lines)


if __name__ == "__main__":
    DB_PATH = os.getenv("DB_PATH", "rh_radar.db")
    conn = sqlite3.connect(DB_PATH)
    print(build_report(conn))
    conn.close()