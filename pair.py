"""
RH Radar - Diagnostic: print raw pair JSON for one known Robinhood Chain token
so we can see DexScreener's actual field names instead of guessing.
"""

import json
import requests

CHAIN_ID = "robinhood"
TOKEN_PAIRS_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

# Use the profiles endpoint to grab one fresh Robinhood Chain token address
PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

resp = requests.get(PROFILES_URL, timeout=15)
resp.raise_for_status()
profiles = resp.json()

rh_tokens = [p for p in profiles if p.get("chainId") == CHAIN_ID]

if not rh_tokens:
    print("No Robinhood Chain tokens in this batch, run again in a bit.")
else:
    sample_address = rh_tokens[0].get("tokenAddress")
    print(f"Sample token profile entry:\n{json.dumps(rh_tokens[0], indent=2)}\n")
    print(f"Using tokenAddress: {sample_address}\n")

    pairs_resp = requests.get(TOKEN_PAIRS_URL.format(address=sample_address), timeout=15)
    pairs_resp.raise_for_status()
    data = pairs_resp.json()
    pairs = data.get("pairs") or []

    if not pairs:
        print("No pairs returned for this token.")
    else:
        print(f"Raw JSON of first pair returned:\n{json.dumps(pairs[0], indent=2)}")