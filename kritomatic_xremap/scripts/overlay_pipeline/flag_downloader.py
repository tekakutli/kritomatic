#!/usr/bin/env python3
"""
Country Flag Emoji Downloader
Converts country flag emojis to PNG images with specified size.
"""

import os
import sys
import requests
from PIL import Image
from io import BytesIO
import re

# Your specific country list (Spanish names)
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
    "Bélgica"
]

# Image settings
IMAGE_SIZE = (128, 128)  # Width, Height in pixels
OUTPUT_DIR = "/tmp/flags"

# Extended country code mapping with Spanish names
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
    "paraguay": "py",  # Added Paraguay
    "bélgica": "be",   # Added Bélgica with accent
    "belgica": "be",   # Added Bélgica without accent

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
    "turkey": "tr"
}


def get_country_code(country_name):
    """Convert country name to ISO country code (case insensitive)."""
    country_lower = country_name.lower().strip()

    # Remove accents for better matching
    import unicodedata
    country_normalized = ''.join(
        c for c in unicodedata.normalize('NFD', country_lower)
        if unicodedata.category(c) != 'Mn'
    )

    # Direct lookup
    if country_lower in COUNTRY_CODES:
        return COUNTRY_CODES[country_lower]

    # Try normalized version (without accents)
    if country_normalized in COUNTRY_CODES:
        return COUNTRY_CODES[country_normalized]

    # Try to find partial match (for more flexibility)
    for name, code in COUNTRY_CODES.items():
        if country_lower in name or name in country_lower:
            return code
        if country_normalized in name or name in country_normalized:
            return code

    return None


def get_emoji_url(country_code):
    """Generate URL for flag image using flagcdn.com."""
    # Using the w1600 format for high-quality images
    return f"https://flagcdn.com/w2560/{country_code}.png"


def download_flag(country_code, size=IMAGE_SIZE):
    """Download flag image and return as PIL Image."""
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
    """Remove invalid characters from filename."""
    # Remove accents for filename
    import unicodedata
    name = ''.join(
        c for c in unicodedata.normalize('NFD', name)
        if unicodedata.category(c) != 'Mn'
    )
    # Replace spaces and special chars with underscores
    name = re.sub(r'[^\w\s-]', '', name)
    name = re.sub(r'[-\s]+', '_', name)
    return name.strip('_')


def main():
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Downloading flags for {len(COUNTRIES)} countries...")
    print(f"Image size: {IMAGE_SIZE[0]}x{IMAGE_SIZE[1]} pixels")
    print(f"Output directory: {OUTPUT_DIR}/")
    print("-" * 50)

    success_count = 0
    failed_countries = []

    for i, country in enumerate(COUNTRIES, 1):
        print(f"[{i}/{len(COUNTRIES)}] Processing: {country}", end=" ")

        # Get country code
        country_code = get_country_code(country)
        if not country_code:
            print("❌ (Country code not found)")
            failed_countries.append(country)
            continue

        # Download flag
        img = download_flag(country_code)
        if not img:
            print("❌ (Download failed)")
            failed_countries.append(country)
            continue

        # Create filename: {number}_{country_name}.png
        country_name = sanitize_filename(country)
        filename = f"{i:03d}_{country_name}.png"
        filepath = os.path.join(OUTPUT_DIR, filename)

        # Save image
        img.save(filepath, "PNG")
        print("✅")
        success_count += 1

    # Summary
    print("-" * 50)
    print(f"Summary: {success_count} flags downloaded successfully")

    if failed_countries:
        print(f"Failed: {len(failed_countries)} countries")
        print("Failed countries:", ", ".join(failed_countries))

    print(f"Files saved in: {OUTPUT_DIR}/")


if __name__ == "__main__":
    # Check for required libraries
    try:
        import requests
        from PIL import Image
    except ImportError as e:
        print("Error: Missing required library")
        print("Please install: pip install requests pillow")
        sys.exit(1)

    main()
