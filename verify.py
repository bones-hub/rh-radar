"""
RH Radar - Live Verification
Before showing ANY candidate, double-check with DexScreener's own
pair-lookup API that the pair genuinely still exists. This is what
would have caught the ghost 0x0F1254... address automatically instead
of you having to click through and find "not found" by hand.
"""

import requests

PAIR_LOOKUP_URL = "https://api.dexscreener.com/latest/dex/pairs/robinhood/{pair_address}"


def verify_pair_is_real(pair_address, timeout=8):
    """
    Returns True only if DexScreener's API confirms this exact pair
    still exists and returns real data. Returns False for anything
    ghost, delisted, or malformed - fail closed, not open.
    """
    if not pair_address:
        return False

    try:
        resp = requests.get(PAIR_LOOKUP_URL.format(pair_address=pair_address), timeout=timeout)
        if resp.status_code != 200:
            return False
        data = resp.json()
        pairs = data.get("pairs") or data.get("pair")
        if not pairs:
            return False
        return True
    except requests.RequestException:
        # If we can't verify, treat it as unverified - better to miss a
        # real one than show a possibly-fake one.
        return False
