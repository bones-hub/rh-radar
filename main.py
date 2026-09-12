"""
RH Radar - Continuous loop
Runs the data collector, then all three lanes, on a repeating interval.
"""

import time
import subprocess
import sys

INTERVAL_SECONDS = 60
SCRIPTS = ["data.py", "undervalued_early.py", "momentum.py", "launches.py"]


def run_script(script):
    print(f"\n▶ Running: {script}\n")
    result = subprocess.run([sys.executable, script], capture_output=False)
    return result.returncode


def main():
    print("=" * 60)
    print("              RH RADAR LIVE")
    print("=" * 60)
    print()
    print(f"Scanning every {INTERVAL_SECONDS} seconds.")
    print("Press CTRL+C to stop.")
    print()

    while True:
        try:
            for script in SCRIPTS:
                result = run_script(script)
                if result != 0:
                    print(f"⚠️ {script} failed (exit code {result}).")

            print()
            print(f"⏳ Waiting {INTERVAL_SECONDS} seconds...")
            time.sleep(INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print()
            print("RH RADAR STOPPED.")
            break


if __name__ == "__main__":
    main()