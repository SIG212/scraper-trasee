#!/usr/bin/env python3
"""
Geocoder pentru trasee.json
Citeste trasee.json, adauga lat/lng pentru fiecare localitate_start
folosind Nominatim (OpenStreetMap), si salveaza inapoi.

Cerinte:
  pip install requests

Nu necesita API key.
"""

import sys
sys.stdout.reconfigure(line_buffering=True)

import json
import time
import requests

INPUT_FILE  = "trasee.json"
OUTPUT_FILE = "trasee.json"  # suprascrie pe loc
SLEEP       = 1.1  # Nominatim ToS: max 1 req/s

HEADERS = {"User-Agent": "MergLaMunte/1.0 geocoder"}


def geocode(localitate: str) -> tuple[float, float] | None:
    """Returneaza (lat, lng) pentru o localitate din Romania sau None."""
    if not localitate or localitate.strip() == "":
        return None

    query = f"{localitate.strip()}, Romania"
    url   = "https://nominatim.openstreetmap.org/search"
    params = {
        "q":            query,
        "format":       "json",
        "limit":        1,
        "countrycodes": "ro",
        "addressdetails": 0,
    }

    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print(f"  [!] Eroare geocodare '{localitate}': {e}", flush=True)

    return None


def main():
    # Citeste trasee.json
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        print(f"[EROARE] {INPUT_FILE} nu exista.", flush=True)
        sys.exit(1)

    trasee = payload.get("trasee", [])
    print(f"Incarcate {len(trasee)} trasee din {INPUT_FILE}", flush=True)

    # Cache localitate -> (lat, lng) ca sa nu apelam de doua ori aceeasi localitate
    cache: dict[str, tuple[float, float] | None] = {}

    found = 0
    skipped = 0
    already = 0

    for i, t in enumerate(trasee):
        # Daca are deja coordonate valide, sari
        if t.get("lat") and t.get("lng"):
            already += 1
            continue

        loc = (t.get("localitate_start") or "").strip()

        if not loc:
            skipped += 1
            continue

        # Foloseste cache
        if loc not in cache:
            time.sleep(SLEEP)
            cache[loc] = geocode(loc)

        coords = cache[loc]

        if coords:
            t["lat"] = coords[0]
            t["lng"] = coords[1]
            found += 1
            print(f"  [{i+1}/{len(trasee)}] {loc} → {coords[0]:.4f}, {coords[1]:.4f}", flush=True)
        else:
            t["lat"] = None
            t["lng"] = None
            skipped += 1
            print(f"  [{i+1}/{len(trasee)}] {loc} → negasit", flush=True)

    # Salveaza
    payload["trasee"] = trasee
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Done!", flush=True)
    print(f"  {already} aveau deja coordonate", flush=True)
    print(f"  {found} geocodate acum", flush=True)
    print(f"  {skipped} fara localitate sau negasite", flush=True)
    print(f"  Salvat in {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
