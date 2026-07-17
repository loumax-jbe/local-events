"""
geocode.py
----------
Turns a town name (e.g. "Princeton, NJ") into lat/lng using OpenStreetMap's
free Nominatim API. No API key required.

Results are cached to data/geocode_cache.json so repeat runs don't hit the
geocoder again for the same town — Nominatim's usage policy asks for no
more than ~1 request/second and discourages re-querying the same thing.
"""

import json
import os
import time

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "geocode_cache.json")
HEADERS = {"User-Agent": "local-event-aggregator/1.0 (personal, low-volume use)"}


def geocode_town(town_name):
    """
    Returns (lat, lng, display_name) for a town name string. Uses a local
    cache when possible so repeat setup runs don't re-hit the API.
    Raises ValueError if the town can't be found.
    """
    cache = _load_cache()
    key = town_name.strip().lower()
    if key in cache:
        entry = cache[key]
        return entry["lat"], entry["lng"], entry["display_name"]

    params = {"q": town_name, "format": "json", "limit": 1}
    resp = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(
            f"Could not find '{town_name}'. Try being more specific, "
            f"e.g. 'Princeton, NJ' instead of just 'Princeton'."
        )

    lat = float(results[0]["lat"])
    lng = float(results[0]["lon"])
    display_name = results[0].get("display_name", town_name)

    cache[key] = {"lat": lat, "lng": lng, "display_name": display_name}
    _save_cache(cache)
    time.sleep(1)  # stay polite to the free public API
    return lat, lng, display_name


def _load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)
