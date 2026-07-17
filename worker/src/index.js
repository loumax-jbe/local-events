/**
 * index.js
 * --------
 * Cloudflare Worker entrypoint. Handles GET /search: geocodes an address,
 * converts a travel-time preference into a search radius (same formula as
 * setup.py), fetches the site's own events.json (built daily by
 * scraper.py from local_sources.yaml/config.yaml — school, library, and
 * other maintainer-curated venues), and returns whichever of those events
 * fall within the requested radius, date range, and categories.
 *
 * This deliberately does not call Ticketmaster/SeatGeek or any other
 * ticketing API — the whole point is "local/smaller events only," pulled
 * from places the site maintainer explicitly added (see
 * local_sources.yaml). No API keys, no secrets: geocoding and the
 * events.json fetch both need none.
 *
 * Non-secret config (wrangler.toml [vars]):
 *   ALLOWED_ORIGIN  — the GitHub Pages origin allowed to call this Worker.
 *   EVENTS_JSON_URL — the published events.json URL to search over.
 */

import { geocodeAddress } from "./geocode.js";
import { distanceMiles } from "./haversine.js";
import { EVENT_TYPES } from "./eventTypes.js";

// Same constants/formula as setup.py's minutes_to_radius().
const DEFAULT_AVG_SPEED_MPH = 30;
const PADDING_FACTOR = 1.3; // roads aren't straight lines
const MIN_RADIUS_MILES = 3;
const MAX_MINUTES = 180;

const DEFAULT_MINUTES = 30;
const DEFAULT_DAYS_AHEAD = 60;
const MAX_DAYS_RANGE = 180; // 6 months — matches the longest preset in setup.py

// Ticketmaster/SeatGeek events can still exist in events.json if the site
// owner has those sources enabled in config.yaml for "Your Local Picks"'s
// own filtering — excluded here too, since this endpoint is specifically
// for local/smaller maintainer-curated venues.
const EXCLUDED_SOURCES = new Set(["Ticketmaster", "SeatGeek"]);

// Cloudflare's fetch-level cache for the upstream events.json — it's one
// shared resource refreshed once a day, so a short edge cache avoids
// re-fetching it on every single search without needing to manage
// caches.default per query.
const EVENTS_JSON_CACHE_SECONDS = 300;

class ValidationError extends Error {}

function minutesToRadius(minutes, avgSpeedMph = DEFAULT_AVG_SPEED_MPH) {
  const radius = (minutes / 60) * avgSpeedMph * PADDING_FACTOR;
  return Math.max(radius, MIN_RADIUS_MILES);
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function addDaysIso(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function isValidDate(s) {
  return /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(new Date(`${s}T00:00:00Z`).getTime());
}

function parseParams(url) {
  const p = url.searchParams;

  const address = (p.get("address") || "").trim();
  if (!address) throw new ValidationError("Missing required parameter: address");
  if (address.length > 200) throw new ValidationError("address is too long");

  let minutes = parseInt(p.get("minutes"), 10);
  if (!Number.isFinite(minutes) || minutes <= 0) minutes = DEFAULT_MINUTES;
  minutes = Math.min(minutes, MAX_MINUTES);

  const today = todayIso();
  const dateFrom = p.get("from") || today;
  let dateTo = p.get("to") || addDaysIso(today, DEFAULT_DAYS_AHEAD);

  if (!isValidDate(dateFrom) || !isValidDate(dateTo)) {
    throw new ValidationError("from/to must be YYYY-MM-DD dates");
  }
  if (dateTo < dateFrom) {
    throw new ValidationError("to must not be before from");
  }
  const maxTo = addDaysIso(dateFrom, MAX_DAYS_RANGE);
  if (dateTo > maxTo) dateTo = maxTo;

  const typesParam = (p.get("types") || "").trim();
  const types = typesParam
    ? typesParam.split(",").map((t) => t.trim()).filter((t) => EVENT_TYPES.includes(t))
    : [];

  return { address, minutes, dateFrom, dateTo, types };
}

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function jsonResponse(body, { status = 200, env } = {}) {
  const headers = { "Content-Type": "application/json", ...corsHeaders(env) };
  return new Response(JSON.stringify(body), { status, headers });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(env) });
    }

    if (url.pathname !== "/search") {
      return jsonResponse({ error: "Not found. Use GET /search." }, { status: 404, env });
    }
    if (request.method !== "GET") {
      return jsonResponse({ error: "Method not allowed" }, { status: 405, env });
    }

    let params;
    try {
      params = parseParams(url);
    } catch (exc) {
      if (exc instanceof ValidationError) {
        return jsonResponse({ error: exc.message }, { status: 400, env });
      }
      throw exc;
    }

    if (!env.EVENTS_JSON_URL) {
      return jsonResponse(
        { error: "Worker is missing EVENTS_JSON_URL — set it in wrangler.toml and redeploy." },
        { status: 500, env }
      );
    }

    let location;
    try {
      location = await geocodeAddress(params.address);
    } catch (exc) {
      return jsonResponse({ error: exc.message }, { status: 400, env });
    }

    let data;
    try {
      const eventsRes = await fetch(env.EVENTS_JSON_URL, {
        cf: { cacheTtl: EVENTS_JSON_CACHE_SECONDS, cacheEverything: true },
      });
      if (!eventsRes.ok) {
        throw new Error(`events.json fetch failed: ${eventsRes.status}`);
      }
      data = await eventsRes.json();
    } catch (exc) {
      return jsonResponse({ error: `Could not load events data: ${exc.message}` }, { status: 502, env });
    }

    const radiusMiles = minutesToRadius(params.minutes);
    const allowedTypes = params.types.length ? new Set(params.types) : null;

    const events = (data.events || []).filter((e) => {
      if (EXCLUDED_SOURCES.has(e.source)) return false;
      if (e.venue_lat == null || e.venue_lng == null) return false;
      if (distanceMiles(location, { lat: e.venue_lat, lng: e.venue_lng }) > radiusMiles) return false;
      // Events with only a date_display (no parseable ISO date, e.g. an
      // all-day ICS entry) can't be checked against the range — keep them
      // rather than silently dropping a genuinely local event.
      if (e.date) {
        const day = e.date.slice(0, 10);
        if (day < params.dateFrom || day > params.dateTo) return false;
      }
      if (allowedTypes && !allowedTypes.has(e.event_type)) return false;
      return true;
    });

    events.sort((a, b) => {
      if (!a.date && !b.date) return 0;
      if (!a.date) return 1;
      if (!b.date) return -1;
      return a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
    });

    const payload = {
      generated_at: new Date().toISOString(),
      location: { lat: location.lat, lng: location.lng, display_name: location.displayName },
      radius_miles: Math.round(radiusMiles * 10) / 10,
      count: events.length,
      events,
    };

    return jsonResponse(payload, { env });
  },
};
