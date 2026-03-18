#!/usr/bin/env python3
"""
build_coords.py - Genereaza coords.json cu coordonate pentru toate
localitatile de start din trasee.json.

Ruleaza O SINGURA DATA (sau cand apar localitati noi):
    python build_coords.py

Foloseste Gemini care cunoaste geografia Romaniei.
Rezultatul e commitat in repo si folosit de scrape_trasee.py.

PROMPT PENTRU GEMINI (ruleaza manual pe aistudio.google.com):
----------------------------------------------------------------
Am nevoie de coordonate GPS precise (lat, lng) pentru urmatoarele
localitati din Romania, folosite ca puncte de start pentru trasee montane.

Pentru fiecare localitate returneaza coordonatele centrului localitatii
sau ale celui mai apropiat punct de acces la munte (parcare, cabana, 
intrare in parc national).

Returneaza DOAR JSON valid, fara text suplimentar, in formatul:
{
  "busteni, prahova": {"lat": 45.4087, "lng": 25.5354},
  "zarnesti, brasov": {"lat": 45.5607, "lng": 25.3197},
  ...
}

Cheia este "localitate, judet" in lowercase, fara diacritice.

Lista de localitati:
LISTA_LOCALITATI_AICI
----------------------------------------------------------------
"""

import json
import os
import sys

def extract_locations(trasee_file: str = "trasee.json") -> list[str]:
    """Extrage toate localitatile unice din trasee.json."""
    try:
        with open(trasee_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Eroare: {trasee_file} nu exista. Ruleaza mai intai scrape_trasee.py")
        sys.exit(1)

    locations = set()
    for t in data.get("trasee", []):
        loc = (t.get("localitate_start") or "").strip().lower()
        judet = (t.get("judet_start") or "").strip().lower()

        if not loc or loc in ("null", "none", ""):
            continue

        if judet and judet not in ("null", "none", ""):
            key = f"{loc}, {judet}"
        else:
            key = loc

        if key:
            locations.add(key)

    return sorted(locations)


def generate_prompt(locations: list[str]) -> str:
    """Genereaza promptul pentru Gemini."""
    lista = "\n".join(f"- {loc}" for loc in locations)
    return f"""Am nevoie de coordonate GPS precise (lat, lng) pentru urmatoarele localitati din Romania, folosite ca puncte de start pentru trasee montane.

Pentru fiecare localitate returneaza coordonatele centrului localitatii sau ale celui mai apropiat punct de acces la munte (parcare, cabana, intrare in parc national). Daca o localitate nu exista in Romania, omite-o.

Returneaza DOAR JSON valid, fara text suplimentar, in formatul exact:
{{
  "busteni, prahova": {{"lat": 45.4087, "lng": 25.5354}},
  "zarnesti, brasov": {{"lat": 45.5607, "lng": 25.3197}}
}}

Cheia este "localitate, judet" exact cum apare in lista de mai jos (lowercase, fara diacritice).

Lista de localitati ({len(locations)} total):
{lista}"""


def validate_coords(coords: dict) -> dict:
    """Valideaza ca coordonatele sunt in Romania (bbox aproximativ)."""
    RO_LAT = (43.5, 48.5)
    RO_LNG = (20.0, 30.0)
    valid = {}
    invalid = []

    for key, val in coords.items():
        try:
            lat = float(val["lat"])
            lng = float(val["lng"])
            if RO_LAT[0] <= lat <= RO_LAT[1] and RO_LNG[0] <= lng <= RO_LNG[1]:
                valid[key] = {"lat": round(lat, 6), "lng": round(lng, 6)}
            else:
                invalid.append(f"{key}: ({lat}, {lng}) - IN AFARA ROMANIEI")
        except (KeyError, TypeError, ValueError) as e:
            invalid.append(f"{key}: eroare format - {e}")

    if invalid:
        print(f"\nATENTIE - {len(invalid)} coordonate invalide (ignorate):")
        for i in invalid:
            print(f"  {i}")

    return valid


def main():
    print("=== build_coords.py ===\n")

    # 1. Extrage localitatile din trasee.json
    trasee_file = sys.argv[1] if len(sys.argv) > 1 else "trasee.json"
    locations = extract_locations(trasee_file)
    print(f"Gasit {len(locations)} localitati unice in {trasee_file}:\n")
    for loc in locations:
        print(f"  - {loc}")

    # 2. Genereaza promptul
    prompt = generate_prompt(locations)

    print("\n" + "="*60)
    print("PROMPT PENTRU GEMINI (copiaza si ruleaza pe aistudio.google.com):")
    print("="*60)
    print(prompt)
    print("="*60)

    # 3. Asteapta JSON-ul de la user
    print("\nDupa ce primesti raspunsul de la Gemini, copiaza JSON-ul")
    print("si salveaza-l in fisierul coords_raw.json")
    print("Apoi ruleaza din nou: python build_coords.py --apply")

    # 4. Daca se ruleaza cu --apply, valideaza si salveaza
    if "--apply" in sys.argv:
        try:
            with open("coords_raw.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            print("\nEroare: coords_raw.json nu exista.")
            print("Salveaza raspunsul Gemini in coords_raw.json si ruleaza din nou cu --apply")
            sys.exit(1)

        print(f"\nValidare {len(raw)} coordonate...")
        valid = validate_coords(raw)

        # Merge cu coords.json existent daca exista
        existing = {}
        try:
            with open("coords.json", "r", encoding="utf-8") as f:
                existing = json.load(f)
            print(f"Merge cu {len(existing)} coordonate existente din coords.json")
        except FileNotFoundError:
            pass

        existing.update(valid)

        with open("coords.json", "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2, sort_keys=True)

        print(f"\nSalvat {len(existing)} coordonate in coords.json")

        # Verifica ce localitati lipsesc inca
        missing = [loc for loc in locations if loc not in existing]
        if missing:
            print(f"\nAtentie: {len(missing)} localitati inca fara coordonate:")
            for m in missing:
                print(f"  - {m}")
        else:
            print("\nToate localitatile au coordonate!")


if __name__ == "__main__":
    main()
