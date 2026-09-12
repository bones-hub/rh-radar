"""
RH Radar - Shared Lane Utilities
One alert history per TOKEN (not per token+lane) - a coin only ever
gets one "NEW CALL" message total, even if it later qualifies for a
different lane too. Milestone alerts reply to the original Telegram
message so they thread together instead of floating as unlinked spam.
"""

from datetime import datetime

MILESTONES = [2, 3, 5, 10, 20, 50, 100]


def init_alert_history(conn):
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alert_history'"
    ).fetchone()

    needs_migration = existing is not None and "token_address, lane" in (existing[0] or "")

    if needs_migration:
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
                last_milestone_hit REAL,
                message_id INTEGER
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
                     last_alert_at, last_alert_mcap, times_alerted, last_milestone_hit, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
                last_milestone_hit REAL,
                message_id INTEGER
            )
        """)
        try:
            conn.execute("ALTER TABLE alert_history ADD COLUMN message_id INTEGER")
        except Exception:
            pass
        conn.commit()


def get_global_duplicate_symbols(conn, collected_at):
    return set(get_global_duplicate_symbol_details(conn, collected_at).keys())


def get_global_duplicate_symbol_details(conn, collected_at=None):
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


def record_message_id(conn, token_address, message_id):
    """Store the Telegram message_id of a token's first ('NEW') alert so
    later milestone alerts can reply/quote it. Only fills it in if empty."""
    if message_id is None:
        return
    conn.execute("""
        UPDATE alert_history SET message_id = ?
        WHERE token_address = ? AND message_id IS NULL
    """, (message_id, token_address))
    conn.commit()


def should_alert(conn, lane, symbol, token_address, market_cap, now_iso):
    """
    Now keyed on token_address ONLY - a token alerted in one lane will
    never trigger a second "NEW" alert just because it later qualifies
    for a different lane too.

    Returns (should_show, is_repeat, times_alerted, milestone_info, parent_message_id).
    parent_message_id is the Telegram message_id of the original NEW
    alert, used to reply/thread milestone messages under it.
    """
    row = conn.execute("""
        SELECT first_alert_at, first_alert_mcap, times_alerted, last_milestone_hit, message_id
        FROM alert_history WHERE token_address = ?
    """, (token_address,)).fetchone()

    if row is None:
        conn.execute("""
            INSERT INTO alert_history
                (token_address, lane, symbol, first_alert_at, first_alert_mcap,
                 last_alert_at, last_alert_mcap, times_alerted, last_milestone_hit, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, NULL)
        """, (token_address, lane, symbol, now_iso, market_cap, now_iso, market_cap))
        conn.commit()
        return True, False, 1, None, None

    first_alert_at, first_alert_mcap, times_alerted, last_milestone_hit, message_id = row
    last_milestone_hit = last_milestone_hit or 1

    if not first_alert_mcap or first_alert_mcap <= 0 or not market_cap:
        return False, True, times_alerted, None, message_id

    current_multiple = market_cap / first_alert_mcap

    milestone_hit = None
    for m in MILESTONES:
        if current_multiple >= m and m > last_milestone_hit:
            milestone_hit = m

    if milestone_hit is None:
        return False, True, times_alerted, None, message_id

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
    }, message_id