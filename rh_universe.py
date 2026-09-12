import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UNIVERSE_FILE = os.path.join(DATA_DIR, "rh_universe.json")


# Robinhood-focused crypto universe.
# We will expand and maintain this list as the project grows.
RH_ASSETS = {
    "bitcoin",
    "ethereum",
    "dogecoin",
    "shiba-inu",
    "pepe",
    "bonk",
    "dogwifcoin",
    "floki",
    "popcat",
    "pnut",
    "pengu",
    "moo-deng",
    "cat-in-a-dogs-world",
}


def create_universe():

    os.makedirs(DATA_DIR, exist_ok=True)

    universe = {
        "source": "Robinhood-focused public asset universe",
        "assets": sorted(RH_ASSETS)
    }

    with open(UNIVERSE_FILE, "w", encoding="utf-8") as file:
        json.dump(universe, file, indent=4)

    print("==========================================")
    print("        RH RADAR UNIVERSE CREATED")
    print("==========================================")
    print()
    print(f"Assets saved: {len(RH_ASSETS)}")
    print(f"File: {UNIVERSE_FILE}")
    print()
    print("RH UNIVERSE READY 🚀")


if __name__ == "__main__":
    create_universe()