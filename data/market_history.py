import sqlite3
from datetime import datetime, timezone

DB_PATH = "rh_radar.db"


def create_database():
    connection = sqlite3.connect(DB_PATH)

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            coin_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL,
            market_cap REAL,
            volume_24h REAL,
            change_24h REAL
        )
    """)

    connection.commit()
    connection.close()


def save_observation(coin):
    connection = sqlite3.connect(DB_PATH)

    cursor = connection.cursor()

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
        datetime.now(timezone.utc).isoformat(),
        coin["id"],
        coin["symbol"],
        coin["current_price"],
        coin["market_cap"],
        coin["total_volume"],
        coin["price_change_percentage_24h"]
    ))

    connection.commit()
    connection.close()


if __name__ == "__main__":
    create_database()
    print("RH RADAR DATABASE READY 🧠")