"""
RH Radar - Step 1: Data Layer
Pulls the newest token profiles on Robinhood Chain from DexScreener,
enriches each with real pair data (liquidity, volume, mcap, age),
and saves everything to a local SQLite database.

v2 change: get_latest_robinhood_token_addresses() now retries once on
a 429 instead of letting it crash the whole run_once() call. A single
rate-limit blip on this one endpoint used to kill data.py's entire
exit code for that cycle (no new tokens discovered that minute) - now
it waits briefly and tries again before giving up.
"""

import os
import time
import sqlite3
import requests
from datetime import datetime, timezone

CHAIN_ID = "robinhood"  # DexScreener chainId slug for Robinhood Chain
DB_PATH = os.getenv("DB_PATH", "rh_radar.db")  # /data/rh_radar.db on Railway, local file otherwise
MIN_PRINT_LIQUIDITY = 250  # dust pools below this are saved but not printed individually

# Small pause between each per-token pair request so a batch of ~10-15
# new profiles doesn't fire as one instant burst - this was the main
# thing tripping DexScreener's rate limit and silently dropping most
# of a cycle's new tokens before they ever reached the database.
PACING_SECONDS = 0.35

# Addresses known to be permanently dead/ghost (confirmed via repeated
# 429s or "not found" responses across many cycles) - skip these
# outright instead of burning a request on them every single cycle.
KNOWN_DEAD_TOKENS = {
    "0x0F1254772810EA4D06E5c61E3E4b54d740367Aa8",
}

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


def get_latest_robinhood_token_addresses(retries=2, backoff_seconds=2.0):
    """Fetch the newest token profiles across all chains, filter to Robinhood Chain.

    Retries on a 429 (rate-limited) instead of raising immediately - a
    brief rate-limit hit on this single endpoint shouldn't take down
    the whole cycle's token discovery.
    """
    for attempt in range(retries + 1):
        resp = requests.get(PROFILES_URL, headers=HEADERS, timeout=15)
        if resp.status_code == 429 and attempt < retries:
            print(f"  Profile endpoint rate-limited, retrying in {backoff_seconds}s...")
            time.sleep(backoff_seconds)
            continue
        resp.raise_for_status()
        profiles = resp.json()

        if not isinstance(profiles, list):
            print("Unexpected response shape from token-profiles endpoint:", profiles)
            return []

        rh_tokens = [p for p in profiles if p.get("chainId") == CHAIN_ID]
        return rh_tokens

    return []


def get_pairs_for_token(token_address):
    """Fetch enriched pair data (liquidity, volume, mcap, age) for one token address."""
    url = TOKEN_PAIRS_URL.format(address=token_address)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("pairs") or []


def get_pairs_for_token_with_retry(token_address, retries=1, backoff_seconds=3.0):
    """
    Same as get_pairs_for_token, but retries once on a 429 with a pause
    first - a single unpaced request used to mean one rate-limit hit
    permanently dropped that token from this cycle's results.
    """
    for attempt in range(retries + 1):
        try:
            return get_pairs_for_token(token_address)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 429 and attempt < retries:
                time.sleep(backoff_seconds)
                continue
            raise


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
    fetchable_tokens = [t for t in rh_tokens if t.get("tokenAddress") not in KNOWN_DEAD_TOKENS]
    skipped_dead = len(rh_tokens) - len(fetchable_tokens)
    if skipped_dead:
        print(f"  (Skipping {skipped_dead} known-dead address(es) - not re-fetching every cycle.)")

    for i, token in enumerate(fetchable_tokens):
        address = token.get("tokenAddress")
        if not address:
            continue

        try:
            pairs = get_pairs_for_token_with_retry(address)
        except requests.RequestException as e:
            print(f"  Failed to fetch pairs for {address}: {e}")
            if i < len(fetchable_tokens) - 1:
                time.sleep(PACING_SECONDS)
            continue

        if not pairs:
            print(f"  {address}: profile exists but no trading pair found yet, skipping.")
            if i < len(fetchable_tokens) - 1:
                time.sleep(PACING_SECONDS)
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

        if i < len(fetchable_tokens) - 1:
            time.sleep(PACING_SECONDS)

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