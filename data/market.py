import os
import sqlite3
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

API_KEY = os.getenv("COINGECKO_API_KEY")

BASE_URL = "https://api.coingecko.com/api/v3"
DB_PATH = "rh_radar.db"


def get_market_data():
    all_coins = []

    # Collect several pages instead of only the top 20.
    for page in range(1, 6):

        url = f"{BASE_URL}/coins/markets"

        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 100,
            "page": page,
            "sparkline": "false",
        }

        headers = {
            "x-cg-demo-api-key": API_KEY
        }

        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=15
        )

        response.raise_for_status()

        coins = response.json()

        if not coins:
            break

        all_coins.extend(coins)

        print(f"Collected page {page}: {len(coins)} coins")

    return all_coins


def save_observations(coins):

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    timestamp = datetime.now(timezone.utc).isoformat()

    for coin in coins:

        cursor.execute("""
            INSERT INTO market_observations (
                timestamp,
                coin_id,
                symbol,
                price,
                market_cap,
                volume_24h,
                change_24h
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp,
            coin["id"],
            coin["symbol"],
            coin["current_price"],
            coin["market_cap"],
            coin["total_volume"],
            coin["price_change_percentage_24h"]
        ))

    connection.commit()
    connection.close()


def main():

    print("\n========== RH RADAR DATA COLLECTION ==========\n")

    coins = get_market_data()

    print(f"\nTotal coins collected: {len(coins)}")

    save_observations(coins)

    print(f"Saved {len(coins)} observations.")
    print("\nRH RADAR DATA COLLECTION COMPLETE 🚀")


if __name__ == "__main__":
    main()