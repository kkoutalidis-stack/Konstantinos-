"""
Wind alert checker for kiting.live — Paros kite spot.
"""

import json
import os
import re
import sys

import requests
from playwright.sync_api import sync_playwright

URL = "https://kiting.live/kitesurf-spot/paroskite-paros-greece"
THRESHOLD_KNOTS = 25
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE_FILE = "state.json"

WIND_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kt|kts|knots)\b", re.IGNORECASE)


def get_wind_speed_knots() -> float:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        text = page.inner_text("body")
        browser.close()

    print("----- PAGE TEXT (for debugging / first-run verification) -----")
    print(text[:3000])
    print("----------------------------------------------------------------")

    matches = WIND_REGEX.findall(text)
    if not matches:
        raise RuntimeError(
            "Could not find a wind speed value on the page. "
            "Check the page text printed above and update WIND_REGEX."
        )
    return float(matches[0])


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"alerted": False}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_notification(speed: float) -> None:
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set — skipping notification (would have fired).")
        return
    requests.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=f"Άνεμος στο Paros kite spot: {speed:.1f} kt (πάνω από {THRESHOLD_KNOTS}kt)".encode("utf-8"),
        headers={
            "Title": "Wind Alert - Paros".encode("utf-8"),
            "Priority": "high",
            "Tags": "dash",
        },
        timeout=10,
    )


def main() -> None:
    speed = get_wind_speed_knots()
    print(f"Current wind speed: {speed} kt (threshold {THRESHOLD_KNOTS} kt)")

    state = load_state()

    if speed >= THRESHOLD_KNOTS and not state.get("alerted"):
        send_notification(speed)
        state["alerted"] = True
        save_state(state)
        print("Alert sent, state updated.")
    elif speed < THRESHOLD_KNOTS and state.get("alerted"):
        state["alerted"] = False
        save_state(state)
        print("Wind dropped back below threshold, state reset.")
    else:
        print("No state change, no notification needed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
