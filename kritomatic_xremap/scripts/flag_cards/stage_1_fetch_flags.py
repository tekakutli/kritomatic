#!/usr/bin/env python3
"""
Stage 1 — Country Flag Downloader
Converts country flag emojis to PNG images with specified size.
Writes to FLAGS_DIR from paths.py.
"""

import os
import sys
import re
import unicodedata
from io import BytesIO

import requests
from PIL import Image

from paths import FLAGS_DIR

# ===== CONFIGURATION =====
COUNTRIES = [
    "Argentina",
    "Brasil",
    "Canadá",
    "Colombia",
    "Ecuador",
    "Egipto",
    "Inglaterra",
    "Francia",
    "Alemania",
    "Irán",
    "Japón",
    "México",
    "Paises Bajos",
    "Noruega",
    "Portugal",
    "Corea del Sur",
    "España",
    "Suiza",
    "Turquía",
    "Estados Unidos",
    "Paraguay",
    "Bélgica",
]

IMAGE_SIZE = (128, 128)
OUTPUT_DIR = str(FLAGS_DIR)
# =========================

COUNTRY_CODES = {
    # Spanish names
    "argentina": "ar",
    "brasil": "br",
    "canadá": "ca",
    "canada": "ca",
    "colombia": "co",
    "ecuador": "ec",
    "egipto": "eg",
    "inglaterra": "gb",
    "francia": "fr",
    "alemania": "de",
    "irán": "ir",
    "iran": "ir",
    "japón": "jp",
    "japon": "jp",
    "méxico": "mx",
    "mexico": "mx",
    "paises bajos": "nl",
    "paisesbajos": "nl",
    "noruega": "no",
    "portugal": "pt",
    "corea del sur": "kr",
    "coreadelsur": "kr",
    "suiza": "ch",
    "españa": "es",
    "espana": "es",
    "turquía": "tr",
    "turquia": "tr",
    "estados unidos": "us",
    "estadosunidos": "us",
    "paraguay": "py",
    "bélgica": "be",
    "belgica": "be",

    # English names (for compatibility)
    "united states": "us",
    "united states of america": "us",
    "usa": "us",
    "us": "us",
    "united kingdom": "gb",
    "uk": "gb",
    "great britain": "gb",
    "england": "gb",
    "france": "fr",
    "germany": "de",
    "germany (de)": "de",
    "japan": "jp",
    "australia": "au",
    "brazil": "br",
    "india": "in",
    "china": "cn",
    "russia": "ru",
    "italy": "it",
    "spain": "es",
    "mexico": "mx",
    "south korea": "kr",
    "korea": "kr",
    "netherlands": "nl",
    "sweden": "se",
    "norway": "no",
    "denmark": "dk",
    "finland": "fi",
    "portugal": "pt",
    "poland": "pl",
    "ukraine": "ua",
    "argentina": "ar",
    "chile": "cl",
    "colombia": "co",
    "peru": "pe",
    "venezuela": "ve",
    "south africa": "za",
    "egypt": "eg",
    "nigeria": "ng",
    "kenya": "ke",
    "ghana": "gh",
    "morocco": "ma",
    "saudi arabia": "sa",
    "israel": "il",
    "turkey": "tr",
    "iran": "ir",
    "pakistan": "pk",
    "bangladesh": "bd",
    "thailand": "th",
    "vietnam": "vn",
    "indonesia": "id",
    "philippines": "ph",
    "malaysia": "my",
    "singapore": "sg",
    "new zealand": "nz",
    "ireland": "ie",
    "belgium": "be",
    "switzerland": "ch",
    "austria": "at",
    "greece": "gr",
    "czech republic": "cz",
    "hungary": "hu",
    "romania": "ro",
    "canada": "ca",
    "ecuador": "ec",
    "norway": "no",
    "spain": "es",
    "turkey": "tr",
}


def get_country_code(country_name):
    """Convert country name to ISO country code (case insensitive)."""
    country_lower = country_name.lower().strip()
    country_normalized = ''.join(
        c for c in unicodedata.normalize('NFD', country_lower)
        if unicodedata.category(c) != 'Mn'
    )

    if country_lower in COUNTRY_CODES:
        return COUNTRY_CODES[country_lower]
    if country_normalized in COUNTRY_CODES:
        return COUNTRY_CODES[country_normalized]

    for name, code in COUNTRY_CODES.items():
        if country_lower in name or name in country_lower:
            return code
        if country_normalized in name or name in country_normalized:
            return code

    return None


def get_emoji_url(country_code):
    return f"https://flagcdn.com/w2560/{country_code}.png"


def download_flag(country_code, size=IMAGE_SIZE):
    url = get_emoji_url(country_code)
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        if img.size != size:
            img = img.resize(size, Image.Resampling.LANCZOS)
        return img
    except requests.exceptions.RequestException as e:
        print(f"Error downloading flag for {country_code}: {e}")
        return None


def sanitize_filename(name):
    name = ''.join(
        c for c in unicodedata.normalize('NFD', name)
        if unicodedata.category(c) != 'Mn'
    )
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[-\s]+', '_', name)
    return name.strip('_')


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Downloading flags for {len(COUNTRIES)} countries...")
    print(f"Image size: {IMAGE_SIZE[0]}x{IMAGE_SIZE[1]} pixels")
    print(f"Output directory: {OUTPUT_DIR}/")
    print("-" * 50)

    success_count = 0
    failed_countries = []

    for i, country in enumerate(COUNTRIES, 1):
        print(f"[{i}/{len(COUNTRIES)}] Processing: {country}", end=" ")

        country_code = get_country_code(country)
        if not country_code:
            print("❌ (Country code not found)")
            failed_countries.append(country)
            continue

        img = download_flag(country_code)
        if not img:
            print("❌ (Download failed)")
            failed_countries.append(country)
            continue

        country_name = sanitize_filename(country)
        filename = f"{i:03d}_{country_name}.png"
        filepath = os.path.join(OUTPUT_DIR, filename)
        img.save(filepath, "PNG")
        print("✅")
        success_count += 1

    print("-" * 50)
    print(f"Summary: {success_count} flags downloaded successfully")
    if failed_countries:
        print(f"Failed: {len(failed_countries)} countries")
        print("Failed countries:", ", ".join(failed_countries))
    print(f"Files saved in: {OUTPUT_DIR}/")


if __name__ == "__main__":
    try:
        import requests
        from PIL import Image
    except ImportError:
        print("Error: Missing required library")
        print("Please install: pip install requests pillow")
        sys.exit(1)

    main()
