"""
RH Radar - Shared Lane Utilities
One alert history per TOKEN (not per token+lane) - a coin only ever
triggers one "NEW CALL" broadcast total, even if it later qualifies
for a different lane too. This file only tracks WHETHER and WHEN to
alert (first-seen mcap, milestone multiples crossed). Per-subscriber
message IDs (needed to thread milestone replies under each person's
own copy of the original alert) live in subscribers.py instead, since
that's a per-person concern, not a per-token one.
"""

from datetime import datetime

MILESTONES = [2, 3, 5, 10, 20, 50, 100]


def init_alert_history(conn):
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alert_history'"
    ).fetchone()

    needs_lane_migration = existing is not None and "token_address, lane" in (existing[0] or "")

    if needs_lane_migration:
        print("  (migrating alert_history to one-alert-per-token schema...)")
        conn.execute("ALTER TABLE alert_history RENAME TO alert_history_old")
        conn.execute("""
            CREATE TABLE alert_history (
                token_address TEXT PRIMARY KEY,
                lane TEXT,
                symbol TEXT,
                first_alert_at TEXT,
                first_alert_mcap REAL,
                last_alert_at TEXT,
                last_alert_mcap REAL,
                times_alerted INTEGER,
                last_milestone_hit REAL
            )
        """)
        rows = conn.execute("""
            SELECT token_address, lane, symbol, first_alert_at, first_alert_mcap,
                   last_alert_at, last_alert_mcap, times_alerted, last_milestone_hit
            FROM alert_history_old
            ORDER BY first_alert_at ASC
        """).fetchall()
        seen = {}
        for row in rows:
            token_address = row[0]
            if token_address not in seen:
                seen[token_address] = row
        for row in seen.values():
            conn.execute("""
                INSERT OR IGNORE INTO alert_history
                    (token_address, lane, symbol, first_alert_at, first_alert_mcap,
                     last_alert_at, last_alert_mcap, times_alerted, last_milestone_hit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
        conn.execute("DROP TABLE alert_history_old")
        conn.commit()
        print("  (migration complete - one row per token now.)")
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                token_address TEXT PRIMARY KEY,
                lane TEXT,
                symbol TEXT,
                first_alert_at TEXT,
                first_alert_mcap REAL,
                last_alert_at TEXT,
                last_alert_mcap REAL,
                times_alerted INTEGER,
                last_milestone_hit REAL
            )
        """)
        conn.commit()


def get_global_duplicate_symbols(conn, collected_at):
    return set(get_global_duplicate_symbol_details(conn, collected_at).keys())


def get_global_duplicate_symbol_details(conn, collected_at=None):
    """
    Detect symbols that map to more than one DISTINCT token_address,
    checked across ALL raw_pairs history saved so far.
    """
    rows = conn.execute("""
        SELECT symbol, token_address, MAX(market_cap) AS mcap, MAX(liquidity_usd) AS liq
        FROM raw_pairs
        GROUP BY symbol, token_address
    """).fetchall()

    by_symbol = {}
    for symbol, token_address, market_cap, liquidity_usd in rows:
        by_symbol.setdefault(symbol, []).append((token_address, market_cap, liquidity_usd))

    return {sym: addrs for sym, addrs in by_symbol.items() if len(addrs) > 1}


def flag_wash_trading_risk(liquidity_usd, volume_h24):
    if not liquidity_usd or liquidity_usd == 0:
        return False
    return (volume_h24 or 0) / liquidity_usd > 15


def format_duration(seconds):
    """Turn a raw second count into a short human string like '47m' or '2h 15m'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_m = minutes % 60
    if hours < 24:
        return f"{hours}h {rem_m}m" if rem_m else f"{hours}h"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


def should_alert(conn, lane, symbol, token_address, market_cap, now_iso):
    """
    Keyed on token_address ONLY - a token alerted in one lane will never
    trigger a second "NEW" alert just because it later qualifies for a
    different lane too. Only re-alerts on real milestone multiples
    (2x, 3x, 5x, 10x, 20x, 50x, 100x) of its first-seen mcap.

    Returns (should_show, is_repeat, times_alerted, milestone_info).
    """
    row = conn.execute("""
        SELECT first_alert_at, first_alert_mcap, times_alerted, last_milestone_hit
        FROM alert_history WHERE token_address = ?
    """, (token_address,)).fetchone()

    if row is None:
        conn.execute("""
            INSERT INTO alert_history
                (token_address, lane, symbol, first_alert_at, first_alert_mcap,
                 last_alert_at, last_alert_mcap, times_alerted, last_milestone_hit)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)
        """, (token_address, lane, symbol, now_iso, market_cap, now_iso, market_cap))
        conn.commit()
        return True, False, 1, None

    first_alert_at, first_alert_mcap, times_alerted, last_milestone_hit = row
    last_milestone_hit = last_milestone_hit or 1

    if not first_alert_mcap or first_alert_mcap <= 0 or not market_cap:
        return False, True, times_alerted, None

    current_multiple = market_cap / first_alert_mcap

    milestone_hit = None
    for m in MILESTONES:
        if current_multiple >= m and m > last_milestone_hit:
            milestone_hit = m

    if milestone_hit is None:
        return False, True, times_alerted, None

    times_alerted += 1
    try:
        first_dt = datetime.fromisoformat(first_alert_at)
        now_dt = datetime.fromisoformat(now_iso)
        elapsed_seconds = (now_dt - first_dt).total_seconds()
    except (TypeError, ValueError):
        elapsed_seconds = 0

    conn.execute("""
        UPDATE alert_history
        SET last_alert_at = ?, last_alert_mcap = ?, times_alerted = ?, last_milestone_hit = ?
        WHERE token_address = ?
    """, (now_iso, market_cap, times_alerted, milestone_hit, token_address))
    conn.commit()

    return True, True, times_alerted, {
        "multiple": milestone_hit,
        "elapsed_seconds": elapsed_seconds,
        "first_mcap": first_alert_mcap,
    }