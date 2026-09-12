import sqlite3

DB_PATH = "rh_radar.db"


def get_volume_history(coin_id, limit=5):
    """
    Get the most recent volume observations for a coin.
    """

    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            timestamp,
            volume_24h,
            market_cap,
            change_24h
        FROM market_observations
        WHERE coin_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (coin_id, limit))

    rows = cursor.fetchall()

    connection.close()

    return rows


def calculate_volume_acceleration(coin_id):
    """
    Compare the latest volume with the previous observation.
    """

    history = get_volume_history(coin_id)

    if len(history) < 2:
        return None

    latest = history[0]
    previous = history[1]

    latest_volume = latest[1] or 0
    previous_volume = previous[1] or 0

    if previous_volume <= 0:
        return None

    change = (
        (latest_volume - previous_volume)
        / previous_volume
    ) * 100

    return change


def calculate_momentum_score(coin_id):

    volume_change = calculate_volume_acceleration(coin_id)

    if volume_change is None:
        return 0

    if volume_change >= 100:
        return 100

    elif volume_change >= 75:
        return 90

    elif volume_change >= 50:
        return 80

    elif volume_change >= 30:
        return 70

    elif volume_change >= 20:
        return 60

    elif volume_change >= 10:
        return 50

    elif volume_change > 0:
        return 40

    else:
        return 20


def test_coin(coin_id):

    volume_change = calculate_volume_acceleration(coin_id)
    score = calculate_momentum_score(coin_id)

    print("\n========== MOMENTUM TEST ==========\n")

    print(f"Coin: {coin_id}")

    if volume_change is None:
        print("Not enough historical data yet.")
        return

    print(f"Volume change: {volume_change:.2f}%")
    print(f"Momentum score: {score}/100")


if __name__ == "__main__":

    # Test with DOGE first.
    test_coin("dogecoin")