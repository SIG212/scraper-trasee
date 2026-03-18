#!/usr/bin/env python3
"""
Scraper trasee montane Romania
Surse: bloguldecalatorii.ro, thechillinbear.com, chitaracalatoare.ro,
       jurnaldedrumetii.ro, suspemunte.com
Output: trasee.json

Campuri extrase per traseu:
  nume, localitate_start, durata_h, dificultate,
  denivelare_m, distanta_km, sursa_url, poza_url

Cerinte:
  pip install requests beautifulsoup4 google-genai

Variabile de mediu necesare:
  GEMINI_API_KEY
"""

import sys
sys.stdout.reconfigure(line_buffering=True)

import os
import re
import json
import time
import datetime

import requests
from bs4 import BeautifulSoup
from google import genai

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}

GEMINI_API_KEY      = os.environ.get("GEMINI_API_KEY", "")
gemini_client       = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
_last_gemini_call   = 0.0

MAX_PER_SOURCE      = 999    # limita articole per sursa (coboara la 10 pentru test rapid)
SLEEP_HTTP          = 1.5    # pauza intre request-uri HTTP (politete fata de servere)
GEMINI_INTERVAL     = 1.0    # rate-limit: 1 req/s (Tier-1 = 150 RPM)
GEMINI_MODEL        = "gemini-2.5-flash-lite"  # schimba cu "gemini-2.5-pro" pentru calitate mai buna
OUTPUT_FILE         = "trasee.json"


# ---------------------------------------------------------------------------
# Gemini: extragere structurata
# ---------------------------------------------------------------------------

PROMPT = """\
Esti un asistent care extrage informatii structurate despre trasee montane din Romania.
Articolele sunt jurnale de drumetie — datele tehnice pot aparea oriunde in text, nu doar la inceput.

Titlu: {title}
URL: {url}

Text:
{text}

Cauta cu atentie in TOT textul urmatoarele informatii si raspunde DOAR cu JSON valid, fara text suplimentar, fara backticks:

{{
  "nume": "numele scurt al traseului sau titlul articolului reformulat concis",
  "localitate_start": "satul sau orasul de unde pleaca traseul (fara diacritice, ex: Busteni, Zarnesti, Sinaia)",
  "durata_h": numar_float_sau_null,
  "dificultate": "usor|mediu|greu|null",
  "denivelare_m": numar_int_sau_null,
  "distanta_km": numar_float_sau_null
}}

Reguli stricte:
- durata_h: cauta "ore", "h", "timp de mers", "durata" — converteste minute in ore (ex: 90 min = 1.5)
- distanta_km: cauta "km", "kilometri", "distanta"
- denivelare_m: cauta "denivelare", "diferenta de nivel", "D+", "urcare", "metri"
- dificultate: normalizeaza la usor / mediu / greu (ex: "moderat" -> "mediu", "dificil" -> "greu")
- localitate_start: fara diacritice (Busteni nu Bușteni, Brasov nu Brașov)
- Nu inventa date. Daca nu gasesti o informatie, pune null.
"""


def extract_with_gemini(title: str, text: str, url: str) -> dict | None:
    global _last_gemini_call

    if not gemini_client:
        print("  [!] GEMINI_API_KEY lipsa — skip AI", flush=True)
        return None

    for attempt in range(3):
        try:
            elapsed = time.time() - _last_gemini_call
            if elapsed < GEMINI_INTERVAL:
                time.sleep(GEMINI_INTERVAL - elapsed)

            _last_gemini_call = time.time()

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=PROMPT.format(title=title, url=url, text=text[:8000]),
            )
            raw = response.text.strip()
            raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()
            return json.loads(raw)

        except Exception as e:
            err = str(e)
            if "429" in err:
                wait = 30 * (attempt + 1)
                print(f"  [!] Rate limit Gemini, astept {wait}s...", flush=True)
                time.sleep(wait)
            else:
                print(f"  [!] Eroare Gemini: {e}", flush=True)
                return None

    return None


# ---------------------------------------------------------------------------
# Utilitare HTML
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 15) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [!] HTTP eroare {url}: {e}", flush=True)
        return None


def og_image(soup: BeautifulSoup) -> str | None:
    tag = soup.find("meta", property="og:image")
    return tag.get("content") if tag else None


def body_text(soup: BeautifulSoup) -> str:
    node = soup.find("div", class_="entry-content") or soup.find("article")
    return node.get_text(separator=" ", strip=True) if node else ""


def h1_text(soup: BeautifulSoup) -> str:
    tag = soup.find("h1")
    return tag.get_text(strip=True) if tag else ""


def build_record(data: dict, url: str, blog: str, img: str | None) -> dict:
    """Combina datele Gemini cu metadatele sursei."""
    return {
        "nume":             data.get("nume"),
        "localitate_start": data.get("localitate_start"),
        "durata_h":         data.get("durata_h"),
        "dificultate":      data.get("dificultate"),
        "denivelare_m":     data.get("denivelare_m"),
        "distanta_km":      data.get("distanta_km"),
        "sursa_url":        url,
        "sursa_blog":       blog,
        "poza_url":         img,
    }


def scrape_articles(links: list[str], blog_name: str) -> list[dict]:
    """Scrapeaza o lista de URL-uri si returneaza traseele extrase."""
    results = []
    total = min(len(links), MAX_PER_SOURCE)

    for i, url in enumerate(links[:MAX_PER_SOURCE]):
        print(f"  [{i+1}/{total}] {url}", flush=True)
        time.sleep(SLEEP_HTTP)

        r = http_get(url)
        if not r:
            continue

        soup  = BeautifulSoup(r.text, "html.parser")
        title = h1_text(soup)
        text  = body_text(soup)
        img   = og_image(soup)

        if not text:
            print("    skip: fara continut", flush=True)
            continue

        data = extract_with_gemini(title, text, url)
        if not data:
            continue

        record = build_record(data, url, blog_name, img)
        results.append(record)
        print(f"    OK: {record['nume'] or '(fara nume)'} | "
              f"{record['localitate_start'] or '?'} | "
              f"{record['durata_h']}h | "
              f"{record['distanta_km']}km | "
              f"{record['dificultate']}", flush=True)

    return results


# ---------------------------------------------------------------------------
# Scraper 1: bloguldecalatorii.ro
# ---------------------------------------------------------------------------

def scrape_bloguldecalatorii() -> list[dict]:
    print("\n[1/5] bloguldecalatorii.ro", flush=True)
    index = "https://bloguldecalatorii.ro/articole/idei-de-ture-pe-munte-clasificate-pe-munti"

    r = http_get(index)
    if not r:
        return []

    soup  = BeautifulSoup(r.text, "html.parser")
    block = soup.find("div", class_="entry-content") or soup.find("article")
    links = []

    if block:
        for a in block.find_all("a", href=True):
            href = a["href"]
            if "bloguldecalatorii.ro" in href and "/20" in href and "#" not in href:
                links.append(href)

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)
    return scrape_articles(links, "bloguldecalatorii")


# ---------------------------------------------------------------------------
# Scraper 2: thechillinbear.com
# ---------------------------------------------------------------------------

def scrape_thechillinbear() -> list[dict]:
    print("\n[2/5] thechillinbear.com", flush=True)
    index = "https://ro.thechillinbear.com/trasee/"

    r = http_get(index)
    if not r:
        return []

    soup  = BeautifulSoup(r.text, "html.parser")
    skip  = {
        "https://ro.thechillinbear.com/trasee/",
        "https://ro.thechillinbear.com/",
        "https://ro.thechillinbear.com/cabane/",
        "https://ro.thechillinbear.com/map/",
        "https://ro.thechillinbear.com/experiente/",
        "https://ro.thechillinbear.com/fotografii/",
    }
    links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if (
            href.startswith("https://ro.thechillinbear.com/")
            and "/category/" not in href
            and "/author/" not in href
            and "/page/" not in href
            and href not in skip
            and href.count("/") == 4
            and "#" not in href
        ):
            links.append(href)

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)
    return scrape_articles(links, "thechillinbear")


# ---------------------------------------------------------------------------
# Scraper 3: chitaracalatoare.ro
# ---------------------------------------------------------------------------

def scrape_chitaracalatoare() -> list[dict]:
    print("\n[3/5] chitaracalatoare.ro", flush=True)
    links = []

    for page in range(1, 20):
        url = (
            "https://chitaracalatoare.ro/categorie/munte-romania/"
            if page == 1
            else f"https://chitaracalatoare.ro/categorie/munte-romania/page/{page}/"
        )
        r = http_get(url)
        if not r or r.status_code == 404:
            break

        soup  = BeautifulSoup(r.text, "html.parser")
        found = False

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "chitaracalatoare.ro/20" in href and "#" not in href and href not in links:
                links.append(href)
                found = True

        if not found:
            break
        time.sleep(1)

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)
    return scrape_articles(links, "chitaracalatoare")


# ---------------------------------------------------------------------------
# Scraper 4: jurnaldedrumetii.ro
# ---------------------------------------------------------------------------

def scrape_jurnaldedrumetii() -> list[dict]:
    print("\n[4/5] jurnaldedrumetii.ro", flush=True)
    links = []

    for page in range(1, 20):
        url = (
            "https://jurnaldedrumetii.ro/category/drumetii/"
            if page == 1
            else f"https://jurnaldedrumetii.ro/category/drumetii/page/{page}/"
        )
        r = http_get(url)
        if not r:
            break

        soup  = BeautifulSoup(r.text, "html.parser")
        found = False

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if (
                "jurnaldedrumetii.ro" in href
                and "/category/" not in href
                and "/author/" not in href
                and "/page/" not in href
                and "/tag/" not in href
                and href.startswith("https://jurnaldedrumetii.ro/")
                and href.count("/") >= 4
                and "#" not in href
                and href not in links
            ):
                links.append(href)
                found = True

        if not found:
            break
        time.sleep(1)

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)
    return scrape_articles(links, "jurnaldedrumetii")


# ---------------------------------------------------------------------------
# Scraper 5: suspemunte.com
# ---------------------------------------------------------------------------

def scrape_suspemunte() -> list[dict]:
    print("\n[5/5] suspemunte.com", flush=True)
    links = []

    for page in range(1, 20):
        url = (
            "https://suspemunte.com/drumetii/"
            if page == 1
            else f"https://suspemunte.com/drumetii/page/{page}/"
        )
        r = http_get(url)
        if not r:
            break

        soup  = BeautifulSoup(r.text, "html.parser")
        found = False

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if (
                "suspemunte.com" in href
                and "/drumetii/" in href
                and href != "https://suspemunte.com/drumetii/"
                and "/page/" not in href
                and "/category/" not in href
                and "#" not in href
                and href not in links
            ):
                links.append(href)
                found = True

        if not found:
            break
        time.sleep(1)

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)
    return scrape_articles(links, "suspemunte")


# ---------------------------------------------------------------------------
# Deduplicare
# ---------------------------------------------------------------------------

def deduplicate(trasee: list[dict]) -> list[dict]:
    """
    Elimina duplicatele dupa sursa_url.
    Articole cu acelasi (nume, localitate_start) sunt pastrate — pot fi surse diferite
    despre acelasi traseu, utile pentru cross-referinta.
    """
    seen_urls = set()
    unique    = []

    for t in trasee:
        url = t.get("sursa_url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(t)

    return unique


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60, flush=True)
    print("  Scraper trasee montane Romania", flush=True)
    print(f"  Model Gemini: {GEMINI_MODEL}", flush=True)
    print(f"  Max articole/sursa: {MAX_PER_SOURCE}", flush=True)
    print("=" * 60, flush=True)

    if not GEMINI_API_KEY:
        print("\n[EROARE] GEMINI_API_KEY nu e setat. Export variabila si incearca din nou.", flush=True)
        sys.exit(1)

    all_trasee = []
    all_trasee += scrape_bloguldecalatorii()
    all_trasee += scrape_thechillinbear()
    all_trasee += scrape_chitaracalatoare()
    all_trasee += scrape_jurnaldedrumetii()
    all_trasee += scrape_suspemunte()

    # Deduplicare pe URL
    before = len(all_trasee)
    all_trasee = deduplicate(all_trasee)
    after = len(all_trasee)
    print(f"\n[dedup] {before} → {after} trasee (eliminate {before - after} duplicate)", flush=True)

    # Filtreaza intrari fara date minime (cel putin nume sau localitate)
    valid = [
        t for t in all_trasee
        if t.get("nume") or t.get("localitate_start")
    ]

    output = {
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "model":      GEMINI_MODEL,
        "count":      len(valid),
        "trasee":     valid,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Done! {len(valid)} trasee salvate in {OUTPUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
