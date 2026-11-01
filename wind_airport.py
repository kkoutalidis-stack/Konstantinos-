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
        raise ValueError(f"Δεν
