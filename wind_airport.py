"""
Wind alert για την Πούντα (Πάρος) — δεδομένα από τον σταθμό του αεροδρομίου Πάρου (LGPA).

Πηγή: επίσημες μετρήσεις METAR της NOAA (ανανέωση κάθε 30', στις :20 και :50).

Συμπεριφορά:
- Ειδοποίηση στο κινητό (ntfy) όταν ο μέσος άνεμος φτάσει τους THRESHOLD_KNOTS.
- Μία ειδοποίηση όταν ξαναπέσει κάτω από (THRESHOLD_KNOTS - HYSTERESIS_KNOTS).
- Το πρόγραμμα ΔΕΝ αποτυγχάνει ποτέ (άρα δεν στέλνει email το GitHub).
  Αν δεν βρει μετρήσεις για NO_DATA_HOURS ώρες, στέλνει μία ειδοποίηση στο κινητό.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

STATION = "LGPA"
THRESHOLD_KNOTS = 20
HYSTERESIS_KNOTS = 2          # πέφτει "κάτω" στους 18, για να μη χτυπάει συνέχεια γύρω στους 20
MAX_OBS_AGE_MIN = 90          # μέτρηση παλαιότερη από αυτό δεν θεωρείται τρέχουσα
NO_DATA_HOURS = 6             # μετά από τόσες ώρες χωρίς δεδομένα -> μία ειδοποίηση

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
STATE_FILE = "state_airport.json"

SOURCES = [
    ("aviationweather", f"https://aviationweather.gov/api/data/metar?ids={STATION}&format=json"),
    ("noaa-tgftp", f"https://tgftp.nws.noaa.gov/data/observations/metar/stations/{STATION}.TXT"),
]

WIND_RE = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b")
TIME_RE = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")

COMPASS = ["Β", "ΒΑ", "Α", "ΝΑ", "Ν", "ΝΔ", "Δ", "ΒΔ"]


# ---------- ανάγνωση METAR ----------

def parse_metar(raw: str, now: datetime) -> dict:
    """Βγάζει ώρα, κατεύθυνση, άνεμο και ριπή από ένα raw METAR."""
    t = TIME_RE.search(raw)
    w = WIND_RE.search(raw)
    if not t or not w:
        raise ValueError("Δεν διαβάζεται το METAR: " + repr(raw))

    day, hour, minute = int(t.group(1)), int(t.group(2)), int(t.group(3))
    obs = now.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
    # ημέρα του μήνα· αν είναι "στο μέλλον", ανήκει στον προηγούμενο μήνα
    try:
        obs = obs.replace(day=day)
    except ValueError:
        obs = (obs - timedelta(days=1)).replace(day=day)
    if obs > now + timedelta(hours=1):
        prev_month_end = obs.replace(day=1) - timedelta(days=1)
        obs = prev_month_end.replace(day=day, hour=hour, minute=minute)

    direction = None if w.group(1) == "VRB" else int(w.group(1))
    return {
        "raw": raw.strip(),
        "time": obs,
        "dir": direction,
        "speed": int(w.group(2)),
        "gust": int(w.group(3)) if w.group(3) else None,
    }


def fetch_raw(name: str, url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent": "paros-wind-alert"})
    r.raise_for_status()
    if name == "aviationweather":
        data = r.json()
        if not data:
            raise ValueError("empty response")
        return data[0]["rawOb"]
    # tgftp: 1η γραμμή ημερομηνία, 2η γραμμή το METAR
    lines = [l for l in r.text.splitlines() if l.strip()]
    return lines[-1]


def get_observation(now: datetime) -> dict:
    errors = []
    for name, url in SOURCES:
        try:
            obs = parse_metar(fetch_raw(name, url), now)
            print(f"[{name}] {obs['raw']}")
            return obs
        except Exception as e:  # δοκιμάζουμε την επόμενη πηγή
            errors.append(f"{name}: {e}")
    raise RuntimeError(" | ".join(errors))


# ---------- κατάσταση ----------

def load_state() -> dict:
    state = {"alerted": False, "fail_count": 0, "nodata_alerted": False}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state.update(json.load(f))
        except Exception:
            pass
    return state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


# ---------- ειδοποιήσεις ----------

def notify(title: str, message: str, tags: str, priority: str = "high") -> None:
    if not NTFY_TOPIC:
        print(f"(no NTFY_TOPIC) would send: {title} - {message}")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Priority": priority, "Tags": tags},
            timeout=10,
        )
    except Exception as e:
        print(f"ntfy failed: {e}")


def describe(obs: dict) -> str:
    if obs["speed"] == 0:
        d = "νηνεμία"
    elif obs["dir"] is None:
        d = "μεταβλητός"
    else:
        d = f"{COMPASS[round(obs['dir'] / 45) % 8]} ({obs['dir']}°)"
    gust = f", ριπές {obs['gust']} kt" if obs["gust"] else ""
    local = obs["time"].astimezone(ZoneInfo("Europe/Athens"))
    return f"{obs['speed']} kt{gust}, {d} — μέτρηση {local:%H:%M}"


# ---------- κύριο ----------

def main() -> None:
    now = datetime.now(timezone.utc)
    state = load_state()
    original = json.dumps(state, sort_keys=True)

    try:
        obs = get_observation(now)
        age_min = (now - obs["time"]).total_seconds() / 60
        if age_min > MAX_OBS_AGE_MIN:
            raise RuntimeError(f"old observation ({age_min:.0f} min)")
    except Exception as e:
        print(f"No current observation: {e}")
        state["fail_count"] = state.get("fail_count", 0) + 1
        if not state["nodata_alerted"] and state["fail_count"] >= NO_DATA_HOURS * 2:  # 2 έλεγχοι/ώρα
            notify("Wind Alert - Πάρος",
                   f"Δεν έρχονται μετρήσεις από το αεροδρόμιο εδώ και {NO_DATA_HOURS}+ ώρες.",
                   "warning", priority="default")
            state["nodata_alerted"] = True
        if json.dumps(state, sort_keys=True) != original:
            save_state(state)
        return

    print(f"Άνεμος: {describe(obs)} (όριο {THRESHOLD_KNOTS} kt)")

    # Ξαναήρθαν δεδομένα μετά από διακοπή
    state["fail_count"] = 0
    state["nodata_alerted"] = False

    speed = obs["speed"]
    if speed >= THRESHOLD_KNOTS and not state["alerted"]:
        notify("Φυσάει στην Πούντα! 🪁", describe(obs), "dash")
        state["alerted"] = True
    elif speed < THRESHOLD_KNOTS - HYSTERESIS_KNOTS and state["alerted"]:
        notify("Ο αέρας έπεσε", describe(obs), "arrow_down_small", priority="default")
        state["alerted"] = False

    if json.dumps(state, sort_keys=True) != original:
        save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Ποτέ exit code 1: δεν θέλουμε email σφάλματος από το GitHub.
        print(f"Unexpected error: {e}")
