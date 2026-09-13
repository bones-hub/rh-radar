"""
RH Radar - Step 1: Data Layer
Pulls the newest token profiles on Robinhood Chain from DexScreener,
enriches each with real pair data (liquidity, volume, mcap, age),
and saves everything to a local SQLite database.
"""

import os
import sqlite3
import requests
from datetime import datetime, timezone

CHAIN_ID = "robinhood"  # DexScreener chainId slug for Robinhood Chain
DB_PATH = os.getenv("DB_PATH", "rh_radar.db")  # /data/rh_radar.db on Railway, local file otherwise
MIN_PRINT_LIQUIDITY = 250  # dust pools below this are saved but not printed individually

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_PAIRS_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

HEADERS = {"User-Agent": "rh-radar/0.1"}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT,
            token_address TEXT,
            pair_address TEXT,
            symbol TEXT,
            dex_id TEXT,
            price_usd REAL,
            liquidity_usd REAL,
            volume_h24 REAL,
            price_change_h24 REAL,
            fdv REAL,
            market_cap REAL,
            pair_created_at TEXT,
            pair_age_hours REAL,
            dex_url TEXT
        )
    """)
    conn.commit()
    return conn


def get_latest_robinhood_token_addresses():
    """Fetch the newest token profiles across all chains, filter to Robinhood Chain."""
    resp = requests.get(PROFILES_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    profiles = resp.json()

    if not isinstance(profiles, list):
        print("Unexpected response shape from token-profiles endpoint:", profiles)
        return []

    rh_tokens = [p for p in profiles if p.get("chainId") == CHAIN_ID]
    return rh_tokens


def get_pairs_for_token(token_address):
    """Fetch enriched pair data (liquidity, volume, mcap, age) for one token address."""
    url = TOKEN_PAIRS_URL.format(address=token_address)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("pairs") or []


def pair_age_hours(pair_created_at_ms):
    if not pair_created_at_ms:
        return None
    created = datetime.fromtimestamp(pair_created_at_ms / 1000, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    return round((now - created).total_seconds() / 3600, 2)


def run_once(conn):
    rh_tokens = get_latest_robinhood_token_addresses()
    print(f"Found {len(rh_tokens)} new Robinhood Chain token profile(s) in this fetch.")

    if not rh_tokens:
        print("No Robinhood Chain tokens in the latest profiles batch. "
              "This is normal if launches are infrequent - we'll rerun this on a loop.")
        return

    collected_at = datetime.now(timezone.utc).isoformat()
    saved = 0
    dust_skipped_prints = 0

    for token in rh_tokens:
        address = token.get("tokenAddress")
        if not address:
            continue

        try:
            pairs = get_pairs_for_token(address)
        except requests.RequestException as e:
            print(f"  Failed to fetch pairs for {address}: {e}")
            continue

        if not pairs:
            print(f"  {address}: profile exists but no trading pair found yet, skipping.")
            continue

        for pair in pairs:
            if pair.get("chainId") != CHAIN_ID:
                continue

            base_token = pair.get("baseToken") or {}
            symbol = base_token.get("symbol", "?")
            real_token_address = base_token.get("address") or address
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
                collected_at,
                real_token_address,
                pair.get("pairAddress"),
                symbol,
                pair.get("dexId"),
                pair.get("priceUsd"),
                liquidity_usd,
                volume_h24,
                price_change_h24,
                pair.get("fdv"),
                pair.get("marketCap"),
                datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat() if created_ms else None,
                age_hrs,
                pair.get("url"),
            ))
            saved += 1

            if (liquidity_usd or 0) >= MIN_PRINT_LIQUIDITY:
                age_str = f"{age_hrs}h old" if age_hrs is not None else "age unknown"
                print(f"  {symbol:10s} | liq: ${liquidity_usd or 0:,.0f} | "
                      f"vol24h: ${volume_h24 or 0:,.0f} | mcap: ${pair.get('marketCap') or 0:,.0f} | {age_str}")
            else:
                dust_skipped_prints += 1

    if dust_skipped_prints:
        print(f"  ... plus {dust_skipped_prints} dust pool row(s) under ${MIN_PRINT_LIQUIDITY} "
              f"liquidity (saved to DB, not shown individually)")

    conn.commit()

    # Safety check: remove any token_address shared by more than one
    # distinct symbol in this batch - almost always an unresolved
    # placeholder address, not a real trading pair.
    bad_addresses = conn.execute("""
        SELECT token_address, COUNT(DISTINCT symbol) AS symbol_count
        FROM raw_pairs
        WHERE collected_at = ?
        GROUP BY token_address
        HAVING symbol_count > 1
    """, (collected_at,)).fetchall()

    if bad_addresses:
        for addr, count in bad_addresses:
            print(f"  ⚠️  Removing {addr} - shared by {count} different symbols "
                  f"in this batch, likely an unresolved/placeholder address.")
            conn.execute("""
                DELETE FROM raw_pairs WHERE collected_at = ? AND token_address = ?
            """, (collected_at, addr))
        conn.commit()

    print(f"Saved {saved} pair row(s) to {DB_PATH}.")


if __name__ == "__main__":
    conn = init_db()
    print("RH Radar - Step 1 data layer")
    print(f"Checking DexScreener for newest Robinhood Chain ('{CHAIN_ID}') token profiles...\n")
    run_once(conn)
    conn.close()