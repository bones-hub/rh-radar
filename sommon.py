"""
RH Radar - Shared Lane Utilities
Used by undervalued_early.py, momentum.py, and launches.py so clone
detection and repeat-alert logic exist in exactly one place instead of
three slightly-different copies.
"""

MIN_MCAP_MOVE_TO_REALERT = 0.20  # only show the same candidate again if mcap moved 20%+


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
            PRIMARY KEY (token_address, lane)
        )
    """)
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


def get_global_duplicate_symbol_details(conn, collected_at):
    """
    Same collision check as above, but returns symbol -> list of
    (token_address, market_cap, liquidity_usd) for every colliding
    address, so you can actually see what triggered the exclusion. A
    common ticker (SOL, PEPE, etc.) shared by two unrelated coins looks
    very different from a real clone once you see both rows side by
    side - don't treat every collision as automatically a scam.
    """
    if not collected_at:
        return {}
    rows = conn.execute("""
        SELECT symbol, token_address, market_cap, liquidity_usd
        FROM raw_pairs
        WHERE collected_at = ?
        GROUP BY symbol, token_address
    """, (collected_at,)).fetchall()

    by_symbol = {}
    for symbol, token_address, market_cap, liquidity_usd in rows:
        by_symbol.setdefault(symbol, []).append((token_address, market_cap, liquidity_usd))

    return {sym: addrs for sym, addrs in by_symbol.items() if len(addrs) > 1}


def flag_wash_trading_risk(liquidity_usd, volume_h24):
    if not liquidity_usd or liquidity_usd == 0:
        return False
    return (volume_h24 or 0) / liquidity_usd > 15


def should_alert(conn, lane, symbol, token_address, market_cap, now_iso):
    """
    Decides whether a candidate is worth printing again.
      - Never alerted on this lane before -> yes (first time).
      - Alerted before, mcap hasn't moved meaningfully -> no, it's the
        same repeat clogging up your scan output.
      - Alerted before, mcap moved 20%+ since last alert -> yes,
        something actually changed, worth re-showing.
    Returns (should_show, is_repeat, times_alerted).
    """
    row = conn.execute("""
        SELECT last_alert_mcap, times_alerted
        FROM alert_history WHERE token_address = ? AND lane = ?
    """, (token_address, lane)).fetchone()

    if row is None:
        conn.execute("""
            INSERT INTO alert_history
                (token_address, lane, symbol, first_alert_at, first_alert_mcap,
                 last_alert_at, last_alert_mcap, times_alerted)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, (token_address, lane, symbol, now_iso, market_cap, now_iso, market_cap))
        conn.commit()
        return True, False, 1

    last_mcap, times_alerted = row
    pct_move = abs(market_cap - last_mcap) / last_mcap if last_mcap else 1.0
    times_alerted += 1

    conn.execute("""
        UPDATE alert_history
        SET last_alert_at = ?, last_alert_mcap = ?, times_alerted = ?
        WHERE token_address = ? AND lane = ?
    """, (now_iso, market_cap, times_alerted, token_address, lane))
    conn.commit()

    if pct_move >= MIN_MCAP_MOVE_TO_REALERT:
        return True, True, times_alerted
    return False, True, times_alerted