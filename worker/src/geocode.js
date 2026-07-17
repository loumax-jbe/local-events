/**
 * geocode.js
 * ----------
 * Port of geocode.py — turns an address into lat/lng using OpenStreetMap's
 * free Nominatim API. No API key required.
 *
 * Workers are stateless between cold starts, so there's no disk cache like
 * geocode.py's; a module-level Map gives a free cache hit whenever this
 * isolate is warm (repeated searches for the same address in quick
 * succession), without adding any paid storage.
 */

const NOMINATIM_URL = "https://nominatim.openstreetmap.org/search";
const HEADERS = { "User-Agent": "local-events-search-worker/1.0 (personal, low-volume use)" };

const cache = new Map();

/**
 * Returns { lat, lng, displayName } for an address string.
 * Throws an Error if the address can't be found.
 */
export async function geocodeAddress(address) {
  const key = address.trim().toLowerCase();
  if (cache.has(key)) {
    return cache.get(key);
  }

  const url = new URL(NOMINATIM_URL);
  url.searchParams.set("q", address);
  url.searchParams.set("format", "json");
  url.searchParams.set("limit", "1");

  const resp = await fetch(url.toString(), { headers: HEADERS });
  if (!resp.ok) {
    throw new Error(`Geocoding request failed: ${resp.status}`);
  }
  const results = await resp.json();
  if (!results.length) {
    throw new Error(
      `Could not find "${address}". Try being more specific, e.g. "65 Witherspoon St, Princeton, NJ" instead of just "Princeton".`
    );
  }

  const result = {
    lat: parseFloat(results[0].lat),
    lng: parseFloat(results[0].lon),
    displayName: results[0].display_name || address,
  };
  cache.set(key, result);
  return result;
}
