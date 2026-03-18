#!/usr/bin/env python3
"""
Scraper trasee montane Romania
Surse: bloguldecalatorii.ro, thechillinbear.com, jurnaldedrumetii.ro,
       chitaracalatoare.ro, suspemunte.com
Output: trasee.json
"""

import sys
sys.stdout.reconfigure(line_buffering=True)

import requests
from bs4 import BeautifulSoup
import json
import time
import re
import os
from urllib.parse import urljoin, urlparse
from google import genai

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ---------------------------------------------------------------------------
# Geocoding via Nominatim (OpenStreetMap) - gratuit, fara API key
# ---------------------------------------------------------------------------

def geocode(location_text: str) -> tuple[float, float] | None:
    """Returneaza (lat, lng) pentru un text de locatie, sau None."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location_text + ", Romania", "format": "json", "limit": 1},
            headers={"User-Agent": "MergLaMunte-Scraper/1.0"},
            timeout=10,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print(f"  [geocode] Eroare pentru '{location_text}': {e}", flush=True)
    return None


# ---------------------------------------------------------------------------
# Extragere structurata cu Claude API
# ---------------------------------------------------------------------------

def extract_with_gemini(title: str, text: str, url: str) -> dict | None:
    """Trimite textul unui articol catre Gemini si returneaza date structurate."""
    if not GEMINI_API_KEY:
        print("  [gemini] GEMINI_API_KEY lipsa, sar extragerea AI", flush=True)
        return None

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""Esti un asistent care extrage informatii structurate despre trasee montane din Romania.

Articol: {title}
URL: {url}

Text (primele 3000 caractere):
{text[:3000]}

Extrage urmatoarele informatii si raspunde DOAR cu JSON valid, fara text suplimentar:
{{
  "nume": "numele traseului",
  "munte": "masivul/muntii (ex: Apuseni, Bucegi, Fagaras)",
  "localitate_start": "orasul sau satul cel mai apropiat de punctul de start",
  "km": numar_float_sau_null,
  "durata_h": numar_float_ore_totale_sau_null,
  "denivelare_m": numar_int_metri_sau_null,
  "altitudine_max_m": numar_int_sau_null,
  "dificultate": "usor|mediu|greu|null",
  "zile": numar_int_1_sau_2_sau_3_sau_null,
  "sezon": "vara|iarna|tot_anul|null",
  "tip": "drumetie|circuit|via_ferrata|schi|null",
  "descriere_scurta": "1-2 propozitii despre ce e special la acest traseu"
}}

Daca o informatie nu apare explicit in text, pune null. Nu inventa date."""

    for attempt in range(3):
        try:
            time.sleep(4)  # ~15 req/min pe free tier
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )
            raw = response.text.strip()
            raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
            return json.loads(raw)
        except Exception as e:
            err = str(e)
            if "429" in err:
                wait = 30 * (attempt + 1)
                print(f"  [gemini] Rate limit, astept {wait}s...", flush=True)
                time.sleep(wait)
            else:
                print(f"  [gemini] Eroare extragere: {e}", flush=True)
                return None
    return None


# ---------------------------------------------------------------------------
# Scraper 1: bloguldecalatorii.ro
# ---------------------------------------------------------------------------

def scrape_bloguldecalatorii() -> list[dict]:
    print("\n[1/5] bloguldecalatorii.ro...", flush=True)
    links = []
    index_url = "https://bloguldecalatorii.ro/articole/idei-de-ture-pe-munte-clasificate-pe-munti"

    try:
        r = requests.get(index_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        content = soup.find("div", class_="entry-content") or soup.find("article")
        if content:
            for a in content.find_all("a", href=True):
                href = a["href"]
                if "bloguldecalatorii.ro" in href and "/20" in href:
                    links.append(href)
        links = list(dict.fromkeys(links))  # dedup
        print(f"  Gasit {len(links)} linkuri", flush=True)
    except Exception as e:
        print(f"  Eroare index: {e}", flush=True)
        return []

    results = []
    for i, url in enumerate(links[:80]):  # limit pentru test
        try:
            time.sleep(1.5)
            r = requests.get(url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("h1")
            title_text = title.get_text(strip=True) if title else ""
            content = soup.find("div", class_="entry-content") or soup.find("article")
            body_text = content.get_text(separator=" ", strip=True) if content else ""

            # Incearca sa gaseasca poza principala
            img_url = None
            og_img = soup.find("meta", property="og:image")
            if og_img:
                img_url = og_img.get("content")

            data = extract_with_gemini(title_text, body_text, url)
            if data:
                data["sursa_url"] = url
                data["sursa_blog"] = "bloguldecalatorii"
                data["poza_url"] = img_url
                results.append(data)
                print(f"  [{i+1}/{min(len(links),80)}] OK: {data.get('nume','?')}", flush=True)
            else:
                print(f"  [{i+1}/{min(len(links),80)}] Skip: {url}", flush=True)
        except Exception as e:
            print(f"  Eroare {url}: {e}", flush=True)

    return results


# ---------------------------------------------------------------------------
# Scraper 2: thechillinbear.com
# ---------------------------------------------------------------------------

def scrape_thechillinbear() -> list[dict]:
    print("\n[2/5] thechillinbear.com...", flush=True)
    links = []
    page = 1

    while True:
        try:
            url = f"https://ro.thechillinbear.com/trasee/page/{page}/" if page > 1 else "https://ro.thechillinbear.com/trasee/"
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 404:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            articles = soup.find_all("article")
            if not articles:
                break
            for art in articles:
                a = art.find("a", href=True)
                if a:
                    links.append(a["href"])
            page += 1
            time.sleep(1)
        except Exception as e:
            print(f"  Eroare pagina {page}: {e}", flush=True)
            break

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)

    results = []
    for i, url in enumerate(links):
        try:
            time.sleep(1.5)
            r = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("h1")
            title_text = title.get_text(strip=True) if title else ""

            # thechillinbear are categorii structurate - le extragem direct
            cats = soup.find_all("a", rel="category tag")
            dificultate = None
            munte = None
            for c in cats:
                txt = c.get_text(strip=True).lower()
                if txt in ["ușor", "usor", "mediu", "greu"]:
                    dificultate = txt.replace("ș", "s").replace("u", "u")
                elif any(m in txt for m in ["bucegi", "fagaras", "apuseni", "retezat", "piatra craiului", "ciucas", "parâng", "cindrel"]):
                    munte = c.get_text(strip=True)

            content = soup.find("div", class_="entry-content") or soup.find("article")
            body_text = content.get_text(separator=" ", strip=True) if content else ""

            img_url = None
            og_img = soup.find("meta", property="og:image")
            if og_img:
                img_url = og_img.get("content")

            data = extract_with_gemini(title_text, body_text, url)
            if data:
                # Suprascriem cu datele structurate daca le-am gasit direct
                if dificultate:
                    data["dificultate"] = dificultate
                if munte:
                    data["munte"] = munte
                data["sursa_url"] = url
                data["sursa_blog"] = "thechillinbear"
                data["poza_url"] = img_url
                results.append(data)
                print(f"  [{i+1}/{len(links)}] OK: {data.get('nume','?')}", flush=True)
        except Exception as e:
            print(f"  Eroare {url}: {e}", flush=True)

    return results


# ---------------------------------------------------------------------------
# Scraper 3: chitaracalatoare.ro
# ---------------------------------------------------------------------------

def scrape_chitaracalatoare() -> list[dict]:
    print("\n[3/5] chitaracalatoare.ro...", flush=True)
    links = []
    page = 1

    while page <= 10:  # max 10 pagini
        try:
            url = f"https://chitaracalatoare.ro/categorie/munte-romania/page/{page}/" if page > 1 else "https://chitaracalatoare.ro/categorie/munte-romania/"
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 404:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            found = False
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "chitaracalatoare.ro/20" in href and href not in links:
                    links.append(href)
                    found = True
            if not found:
                break
            page += 1
            time.sleep(1)
        except Exception as e:
            print(f"  Eroare pagina {page}: {e}", flush=True)
            break

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)

    results = []
    for i, url in enumerate(links[:50]):
        try:
            time.sleep(1.5)
            r = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.find("h1")
            title_text = title.get_text(strip=True) if title else ""
            content = soup.find("div", class_="entry-content") or soup.find("article")
            body_text = content.get_text(separator=" ", strip=True) if content else ""

            img_url = None
            og_img = soup.find("meta", property="og:image")
            if og_img:
                img_url = og_img.get("content")

            data = extract_with_gemini(title_text, body_text, url)
            if data:
                data["sursa_url"] = url
                data["sursa_blog"] = "chitaracalatoare"
                data["poza_url"] = img_url
                results.append(data)
                print(f"  [{i+1}/{min(len(links),50)}] OK: {data.get('nume','?')}", flush=True)
        except Exception as e:
            print(f"  Eroare {url}: {e}", flush=True)

    return results


# ---------------------------------------------------------------------------
# Geocoding batch - adauga lat/lng la toate traseele
# ---------------------------------------------------------------------------

def add_coordinates(trasee: list[dict]) -> list[dict]:
    print(f"\n[geocoding] {len(trasee)} trasee...", flush=True)
    cache = {}

    for i, t in enumerate(trasee):
        loc = t.get("localitate_start") or t.get("munte")
        if not loc:
            continue
        if loc in cache:
            t["lat"], t["lng"] = cache[loc]
        else:
            coords = geocode(loc)
            if coords:
                t["lat"], t["lng"] = coords
                cache[loc] = coords
                print(f"  [{i+1}] {loc} → {coords[0]:.4f}, {coords[1]:.4f}", flush=True)
            time.sleep(1.1)  # Nominatim rate limit: 1 req/sec

    return trasee


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Scraper pornit ===", flush=True)
    all_trasee = []

    all_trasee += scrape_bloguldecalatorii()
    all_trasee += scrape_thechillinbear()
    all_trasee += scrape_chitaracalatoare()

    # Adauga coordonate
    all_trasee = add_coordinates(all_trasee)

    # Filtreaza traseele fara date minime
    valid = [t for t in all_trasee if t.get("nume") and (t.get("lat") or t.get("munte"))]

    output = {
        "updated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "count": len(valid),
        "trasee": valid,
    }

    with open("trasee.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nDone! {len(valid)} trasee salvate in trasee.json", flush=True)


if __name__ == "__main__":
    main()
