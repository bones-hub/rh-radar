"""
RH Radar - Live Verification
Before showing ANY candidate, double-check with DexScreener's own
pair-lookup API that the pair genuinely still exists. This is what
would have caught the ghost 0x0F1254... address automatically instead
of you having to click through and find "not found" by hand.

v2 change: a 429 (rate-limited) response used to be treated exactly
like a 404 (pair doesn't exist) - both returned False, which shows up
as "GHOST - excluded" in the lane scripts. That's wrong: a 429 means
"we don't know yet", not "this pair is fake". Real candidates were
getting excluded during rate-limit spikes. Now a 429 gets one short
retry before giving up, so a brief rate-limit blip doesn't silently
drop a real token from that cycle's results.
"""

import time
import requests

PAIR_LOOKUP_URL = "https://api.dexscreener.com/latest/dex/pairs/robinhood/{pair_address}"


def verify_pair_is_real(pair_address, timeout=8, retries=1, backoff_seconds=1.0):
    """
    Returns True only if DexScreener's API confirms this exact pair
    still exists and returns real data. Returns False for anything
    ghost, delisted, or malformed - fail closed, not open.

    A 429 gets `retries` extra attempt(s) with a short pause first,
    since that status means "rate-limited," not "doesn't exist" - the
    two shouldn't be treated the same way.
    """
    if not pair_address:
        return False

    for attempt in range(retries + 1):
        try:
            resp = requests.get(PAIR_LOOKUP_URL.format(pair_address=pair_address), timeout=timeout)
            if resp.status_code == 429 and attempt < retries:
                time.sleep(backoff_seconds)
                continue
            if resp.status_code != 200:
                return False
            data = resp.json()
            pairs = data.get("pairs") or data.get("pair")
            if not pairs:
                return False
            return True
        except requests.RequestException:
            if attempt < retries:
                time.sleep(backoff_seconds)
                continue
            # If we can't verify, treat it as unverified - better to
            # miss a real one than show a possibly-fake one.
            return False

    return False