#!/usr/bin/env python3
"""
Scraper trasee montane Romania
Surse: bloguldecalatorii.ro, chitaracalatoare.ro,
       jurnaldedrumetii.ro, suspemunte.com
Output: trasee.json

Campuri extrase per traseu:
  nume, localitate_start, durata_h, dificultate,
  denivelare_m, distanta_km, elevatie_pozitiva_m, sursa_url, poza_url

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

GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
gemini_client     = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
_last_gemini_call = 0.0

MAX_PER_SOURCE    = 999   # coboara la 3 pentru test rapid (3 x 4 surse = 12 trasee)
SLEEP_HTTP        = 1.5   # pauza intre request-uri HTTP
GEMINI_INTERVAL   = 1.0   # rate-limit: 1 req/s (Tier-1 = 150 RPM)
GEMINI_MODEL      = "gemini-2.5-pro"
OUTPUT_FILE       = "trasee.json"


# ---------------------------------------------------------------------------
# Gemini: extragere structurata
# ---------------------------------------------------------------------------

PROMPT = """\
Esti un asistent care extrage informatii structurate despre trasee montane din Romania.
Articolele sunt jurnale de drumetie — datele tehnice pot aparea oriunde in text.

Titlu: {title}
URL: {url}

Text:
{text}

Cauta cu atentie in TOT textul si raspunde DOAR cu JSON valid, fara text suplimentar, fara backticks:

{{
  "nume": "numele scurt al traseului sau titlul articolului reformulat concis",
  "localitate_start": "satul sau orasul de unde pleaca traseul (fara diacritice, ex: Busteni, Zarnesti, Sinaia)",
  "durata_h": numar_float_ore_sau_null,
  "dificultate": "usor|mediu|greu|null",
  "denivelare_m": numar_int_sau_null,
  "distanta_km": numar_float_sau_null,
  "elevatie_pozitiva_m": numar_int_sau_null
}}

Reguli stricte:
- durata_h: cauta "Durata", "ore", "h", "timp de mers" — converteste (ex: 9h35 = 9.58, 90 min = 1.5)
- distanta_km: cauta "Distanta parcursa", "km", "kilometri"
- denivelare_m: cauta "Diferente de nivel", "diferenta de nivel", "denivelare" — valoarea totala
- elevatie_pozitiva_m: cauta "D+", "Total urcare", "urcare totala", "elevatie pozitiva"
  Daca gasesti doar "Diferente de nivel" fara D+ explicit, foloseste acea valoare si pentru elevatie_pozitiva_m
- dificultate: normalizeaza la usor / mediu / greu (ex: "moderat" -> "mediu", "dificil" -> "greu")
- localitate_start: fara diacritice (Busteni nu Bușteni, Brasov nu Brașov)
- Nu inventa date. Daca nu gasesti o informatie, pune null.
"""


def extract_with_gemini(title: str, text: str, url: str) -> dict | None:
    global _last_gemini_call

    if not gemini_client:
        print("  [!] GEMINI_API_KEY lipsa — skip AI", flush=True)
        return None

    # Trimite inceput + mijloc + final pentru a prinde datele tehnice
    # indiferent de pozitia lor in articol
    if len(text) > 4000:
        mid = len(text) // 2
        text_trimmed = (
            text[:1000]
            + "\n\n[...]\n\n"
            + text[mid - 500 : mid + 500]
            + "\n\n[...]\n\n"
            + text[-1500:]
        )
    else:
        text_trimmed = text

    for attempt in range(3):
        try:
            elapsed = time.time() - _last_gemini_call
            if elapsed < GEMINI_INTERVAL:
                time.sleep(GEMINI_INTERVAL - elapsed)

            _last_gemini_call = time.time()

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=PROMPT.format(title=title, url=url, text=text_trimmed),
            )
            raw = response.text.strip()
            raw = re.sub(r"^```json\s*|^```\s*|```$", "", raw, flags=re.MULTILINE).strip()

            parsed = json.loads(raw)
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed else None
            if not isinstance(parsed, dict):
                print(f"  [!] Tip neasteptat de la Gemini: {type(parsed)}, skip", flush=True)
                return None
            return parsed

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

def http_get(url: str, timeout: int = 15, extra_headers: dict = None) -> requests.Response | None:
    headers = {**HEADERS, **(extra_headers or {})}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [!] HTTP eroare {url}: {e}", flush=True)
        return None


def og_image(soup: BeautifulSoup) -> str | None:
    tag = soup.find("meta", property="og:image")
    return tag.get("content") if tag else None


def body_text(soup: BeautifulSoup) -> str:
    candidates = [
        soup.find("div", class_="entry-content"),
        soup.find("div", class_="post-content"),
        soup.find("div", class_="article-content"),
        soup.find("div", class_="content"),
        soup.find("article"),
        soup.find("main"),
    ]
    node = next((c for c in candidates if c), None)
    return node.get_text(separator=" ", strip=True) if node else ""


def h1_text(soup: BeautifulSoup) -> str:
    tag = soup.find("h1")
    return tag.get_text(strip=True) if tag else ""


def extract_technical_block(text: str) -> str | None:
    """
    Extrage blocul cu date tehnice din articolele jurnaldedrumetii.
    Datele tehnice sunt in format bullet list:
    * Durata: 9h35
    * Distanta parcursa: 17.8km
    * Diferente de nivel: 1037m
    etc.
    """
    match = re.search(
        r'((?:Durata|Distanta|Diferent[ae]|Dificultate).{0,1200})',
        text, re.IGNORECASE | re.DOTALL
    )
    return match.group(1)[:1200] if match else None


def build_record(data: dict, url: str, blog: str, img: str | None) -> dict:
    return {
        "nume":                data.get("nume"),
        "localitate_start":    data.get("localitate_start"),
        "durata_h":            data.get("durata_h"),
        "dificultate":         data.get("dificultate"),
        "denivelare_m":        data.get("denivelare_m"),
        "distanta_km":         data.get("distanta_km"),
        "elevatie_pozitiva_m": data.get("elevatie_pozitiva_m"),
        "sursa_url":           url,
        "sursa_blog":          blog,
        "poza_url":            img,
    }


def scrape_articles(links: list[str], blog_name: str) -> list[dict]:
    results = []
    total   = min(len(links), MAX_PER_SOURCE)

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

        # jurnaldedrumetii: prependuieste blocul tehnic ca sa fie garantat vizibil
        if blog_name == "jurnaldedrumetii":
            tech = extract_technical_block(text)
            if tech:
                text = "=== DATE TEHNICE ===\n" + tech + "\n=== TEXT COMPLET ===\n" + text

        data = extract_with_gemini(title, text, url)
        if not data:
            continue

        record = build_record(data, url, blog_name, img)
        results.append(record)

        durata = f"{record['durata_h']}h"              if record['durata_h']           else "?h"
        dist   = f"{record['distanta_km']}km"           if record['distanta_km']        else "?km"
        elev   = f"D+{record['elevatie_pozitiva_m']}m"  if record['elevatie_pozitiva_m'] else "?D+"
        print(
            f"    OK: {record['nume'] or '(fara nume)'} | "
            f"{record['localitate_start'] or '?'} | "
            f"{durata} | {dist} | {elev} | {record['dificultate'] or '?'}",
            flush=True,
        )

    return results


# ---------------------------------------------------------------------------
# Scraper 1: bloguldecalatorii.ro
# ---------------------------------------------------------------------------

def scrape_bloguldecalatorii() -> list[dict]:
    print("\n[1/4] bloguldecalatorii.ro", flush=True)
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
            if (
                "bloguldecalatorii.ro" in href
                and "/20" in href
                and "#" not in href
                and href.startswith("https://")
            ):
                links.append(href)

    links = list(dict.fromkeys(links))
    print(f"  Gasit {len(links)} linkuri", flush=True)
    return scrape_articles(links, "bloguldecalatorii")


# ---------------------------------------------------------------------------
# Scraper 2: chitaracalatoare.ro
# ---------------------------------------------------------------------------

def scrape_chitaracalatoare() -> list[dict]:
    print("\n[2/4] chitaracalatoare.ro", flush=True)
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
            if (
                "chitaracalatoare.ro/20" in href
                and href.startswith("https://")
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
    return scrape_articles(links, "chitaracalatoare")


# ---------------------------------------------------------------------------
# Scraper 3: jurnaldedrumetii.ro  (Blogger — fara pagini de categorie)
# ---------------------------------------------------------------------------

def scrape_jurnaldedrumetii() -> list[dict]:
    print("\n[3/4] jurnaldedrumetii.ro", flush=True)
    links = []

    for page in range(1, 20):
        if page == 1:
            url = "https://www.jurnaldedrumetii.ro/"
        else:
            url = f"https://www.jurnaldedrumetii.ro/search?updated-max=2099-01-01&max-results=20&start={(page - 1) * 20}"

        r = http_get(url)
        if not r:
            break

        soup  = BeautifulSoup(r.text, "html.parser")
        found = False

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if (
                "jurnaldedrumetii.ro/20" in href
                and href.startswith("http")
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
# Scraper 4: suspemunte.com
# ---------------------------------------------------------------------------

def scrape_suspemunte() -> list[dict]:
    print("\n[4/4] suspemunte.com", flush=True)
    links = []

    extra = {"Referer": "https://www.google.com/"}

    for page in range(1, 20):
        url = (
            "https://suspemunte.com/drumetii/"
            if page == 1
            else f"https://suspemunte.com/drumetii/page/{page}/"
        )
        r = http_get(url, extra_headers=extra)
        if not r:
            print("  suspemunte.com inaccesibil (posibil Cloudflare), skip", flush=True)
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
                and href.startswith("https://")
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
        print("\n[EROARE] GEMINI_API_KEY nu e setat.", flush=True)
        sys.exit(1)

    all_trasee = []
    all_trasee += scrape_bloguldecalatorii()
    all_trasee += scrape_chitaracalatoare()
    all_trasee += scrape_jurnaldedrumetii()
    all_trasee += scrape_suspemunte()

    before = len(all_trasee)
    all_trasee = deduplicate(all_trasee)
    after  = len(all_trasee)
    print(f"\n[dedup] {before} → {after} trasee (eliminate {before - after} duplicate)", flush=True)

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
