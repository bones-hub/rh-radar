"""
RH Radar - Shared Lane Utilities
Used by undervalued_early.py, momentum.py, and launches.py so clone
detection and repeat-alert logic exist in exactly one place instead of
three slightly-different copies.

Repeat-alert logic (v2 - milestone-based):
Instead of re-alerting on any 20%+ mcap wiggle (which spams the same
token over and over as it bounces around), a token now only triggers a
second alert when it actually crosses a real milestone multiple of its
first-seen mcap - 2x, 3x, 5x, 10x, 20x, 50x, 100x. Each milestone only
fires once per token per lane, and the alert reports how long it took
to get there.
"""

from datetime import datetime

MILESTONES = [2, 3, 5, 10, 20, 50, 100]


def init_alert_history(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_history (
            token_address TEXT,
            lane TEXT,
            symbol TEXT,
            first_alert_at TEXT,
            first_alert_mcap REAL,
            last_alert_at TEXT,
            last_alert_mcap REAL,
            times_alerted INTEGER,
            last_milestone_hit REAL,
            PRIMARY KEY (token_address, lane)
        )
    """)
    # Migration path for DBs created before last_milestone_hit existed.
    try:
        conn.execute("ALTER TABLE alert_history ADD COLUMN last_milestone_hit REAL")
    except Exception:
        pass  # column already exists
    conn.commit()


def get_global_duplicate_symbols(conn, collected_at):
    """
    Look for symbols that map to more than one DISTINCT token_address
    anywhere in the latest full batch - not just within one lane's
    filtered candidate list. This is what catches a clone sitting in a
    different mcap band than the real token, which a single lane
    checking only its own candidates would miss entirely.
    """
    return set(get_global_duplicate_symbol_details(conn, collected_at).keys())


def get_global_duplicate_symbol_details(conn, collected_at=None):
    """
    Detect symbols that map to more than one DISTINCT token_address,
    checked across ALL raw_pairs history saved so far - not just the
    single latest data.py fetch (collected_at is kept as a parameter
    for backward compatibility but is no longer used to restrict the
    query).

    This matters because a clone/copycat token reusing a real token's
    ticker won't always show up in the SAME 60-second batch as the
    original - e.g. the real token's pools may have aged out of
    DexScreener's "newest profiles" feed and stopped appearing in new
    fetches, while a fresh clone pool with the same symbol shows up in
    a later cycle. A batch-only check has nothing to compare the clone
    against in that case and lets it straight through. Checking across
    full history closes that gap.
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
    Decides whether a candidate is worth alerting on again.
      - Never alerted on this lane before -> yes (first time, NEW).
      - Alerted before, hasn't crossed a new milestone multiple of its
        first-seen mcap -> no, it's just noise/bouncing.
      - Alerted before, crossed a new milestone (2x, 3x, 5x, 10x, ...)
        it hasn't hit yet -> yes, with the milestone and time-to-reach
        included so the alert actually means something.

    Returns (should_show, is_repeat, times_alerted, milestone_info).
    milestone_info is None on first alert or when not alerting again;
    otherwise a dict: {"multiple": int, "elapsed_seconds": float}.
    """
    row = conn.execute("""
        SELECT first_alert_at, first_alert_mcap, times_alerted, last_milestone_hit
        FROM alert_history WHERE token_address = ? AND lane = ?
    """, (token_address, lane)).fetchone()

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
        # Can't safely compute a multiple - don't spam, just skip.
        return False, True, times_alerted, None

    current_multiple = market_cap / first_alert_mcap

    # Find the highest milestone this candidate has now crossed that it
    # hasn't already been alerted for.
    milestone_hit = None
    for m in MILESTONES:
        if current_multiple >= m and m > last_milestone_hit:
            milestone_hit = m

    if milestone_hit is None:
        # Still tracked, but nothing new worth interrupting you for.
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
        WHERE token_address = ? AND lane = ?
    """, (now_iso, market_cap, times_alerted, milestone_hit, token_address, lane))
    conn.commit()

    return True, True, times_alerted, {
        "multiple": milestone_hit,
        "elapsed_seconds": elapsed_seconds,
        "first_mcap": first_alert_mcap,
    }