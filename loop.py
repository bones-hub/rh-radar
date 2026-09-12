"""
RH Radar - Diagnostic: for every Robinhood Chain token profile in one batch,
print the address we queried with next to what came back in the pair response.
This will show us exactly where (if anywhere) they stop matching.
"""

import requests

CHAIN_ID = "robinhood"
PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_PAIRS_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

resp = requests.get(PROFILES_URL, timeout=15)
resp.raise_for_status()
profiles = resp.json()

rh_tokens = [p for p in profiles if p.get("chainId") == CHAIN_ID]
print(f"Found {len(rh_tokens)} Robinhood Chain profiles in this batch.\n")

for i, token in enumerate(rh_tokens, 1):
    queried_address = token.get("tokenAddress")
    print(f"[{i}] Queried address: {queried_address}")

    try:
        r = requests.get(TOKEN_PAIRS_URL.format(address=queried_address), timeout=15)
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
    except requests.RequestException as e:
        print(f"    ERROR fetching pairs: {e}\n")
        continue

    if not pairs:
        print("    No pairs returned.\n")
        continue

    # Just look at the first pair returned for this token
    p = pairs[0]
    base = p.get("baseToken") or {}
    returned_address = base.get("address")
    returned_symbol = base.get("symbol")

    match = "MATCH" if returned_address == queried_address else "MISMATCH <-- !!"
    print(f"    Returned baseToken address: {returned_address}  symbol: {returned_symbol}  [{match}]\n")