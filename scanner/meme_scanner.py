import sqlite3
import json
import os

DB_PATH = "rh_radar.db"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_FILE = os.path.join(
    BASE_DIR,
    "data",
    "rh_universe.json"
)


def load_rh_universe():

    with open(UNIVERSE_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)

    return set(data["assets"])


def get_latest_market_data():

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    query = """
        SELECT
            coin_id,
            symbol,
            price,
            market_cap,
            volume_24h,
            change_24h,
            timestamp
        FROM market_observations
        WHERE timestamp = (
            SELECT MAX(timestamp)
            FROM market_observations
        )
        AND market_cap IS NOT NULL
        AND volume_24h IS NOT NULL
    """

    cursor.execute(query)

    rows = cursor.fetchall()

    connection.close()

    return rows


def calculate_candidate_score(row):

    coin_id, symbol, price, market_cap, volume, change, timestamp = row

    market_cap = market_cap or 0
    volume = volume or 0
    change = change or 0

    score = 0

    # Market-cap score
    if 10_000_000 <= market_cap <= 1_000_000_000:
        score += 25

    elif 1_000_000_000 < market_cap <= 5_000_000_000:
        score += 10

    elif 1_000_000 <= market_cap < 10_000_000:
        score += 15

    # Volume / market-cap ratio
    volume_ratio = 0

    if market_cap > 0:
        volume_ratio = volume / market_cap

    if volume_ratio >= 0.50:
        score += 25

    elif volume_ratio >= 0.20:
        score += 18

    elif volume_ratio >= 0.10:
        score += 10

    elif volume_ratio >= 0.05:
        score += 5

    # Momentum
    if change >= 10:
        score += 25

    elif change >= 5:
        score += 18

    elif change >= 2:
        score += 10

    elif change > 0:
        score += 5

    # Trading activity
    if volume >= 1_000_000:
        score += 15

    elif volume >= 250_000:
        score += 10

    elif volume >= 100_000:
        score += 5

    return score


def find_candidates():

    rh_universe = load_rh_universe()
    rows = get_latest_market_data()

    candidates = []

    for row in rows:

        coin_id = row[0]

        # Only analyze assets in our RH universe.
        if coin_id not in rh_universe:
            continue

        score = calculate_candidate_score(row)

        candidates.append((score, row))

    candidates.sort(
        reverse=True,
        key=lambda x: x[0]
    )

    return candidates


def display_candidates(candidates):

    print("\n")
    print("==============================================")
    print("          RH RADAR — RH UNIVERSE")
    print("==============================================")
    print()

    if not candidates:
        print("No RH assets found in the latest market snapshot.")
        return

    for score, row in candidates:

        (
            coin_id,
            symbol,
            price,
            market_cap,
            volume,
            change,
            timestamp
        ) = row

        market_cap = market_cap or 0
        volume = volume or 0
        change = change or 0

        volume_ratio = 0

        if market_cap > 0:
            volume_ratio = volume / market_cap

        print(
            f"{symbol.upper():<10} "
            f"SCORE: {score:>3}/90 | "
            f"MC: ${market_cap:,.0f} | "
            f"24h: {change:>6.2f}% | "
            f"VOL: ${volume:,.0f} | "
            f"VOL/MC: {volume_ratio:.2f}"
        )


if __name__ == "__main__":

    candidates = find_candidates()

    display_candidates(candidates)