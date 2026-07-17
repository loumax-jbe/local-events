"""
Ticketmaster Discovery API source.
Docs: https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/
Free tier: 5000 calls/day, no cost.
"""

import requests
from datetime import datetime, timedelta
from normalize import make_event
import classify
import runlog

BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"


def fetch(config):
    api_key = config["api_keys"].get("ticketmaster", "")
    if not api_key or "YOUR_" in api_key:
        print("  [ticketmaster] skipped — no API key set in config.yaml")
        runlog.record("Ticketmaster", status="not_configured", error="No API key set in config.yaml")
        return []

    loc = config["location"]
    end_date = (datetime.utcnow() + timedelta(days=config.get("days_ahead", 60))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cls_cfg = config.get("classification", {})

    events = []
    page = 0
    request_error = None
    while True:
        params = {
            "apikey": api_key,
            "latlong": f"{loc['lat']},{loc['lng']}",
            "radius": loc.get("radius_miles", 25),
            "unit": "miles",
            "endDateTime": end_date,
            "size": 200,
            "page": page,
            "sort": "date,asc",
        }
        try:
            resp = requests.get(BASE_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            print(f"  [ticketmaster] request failed: {exc}")
            request_error = str(exc)
            break

        page_events = data.get("_embedded", {}).get("events", [])
        if not page_events:
            break

        for e in page_events:
            events.append(_to_event(e, cls_cfg))

        total_pages = data.get("page", {}).get("totalPages", 1)
        page += 1
        if page >= total_pages:
            break

    print(f"  [ticketmaster] fetched {len(events)} events")
    if request_error:
        runlog.record("Ticketmaster", status="error", count=len(events), error=request_error)
    elif not events:
        runlog.record("Ticketmaster", status="empty")
    else:
        runlog.record("Ticketmaster", status="ok", count=len(events))
    return events


def _to_event(e, cls_cfg=None):
    cls_cfg = cls_cfg or {}

    venue = None
    try:
        venue = e["_embedded"]["venues"][0]["name"]
    except (KeyError, IndexError):
        pass

    date_iso = None
    date_display = None
    try:
        dates = e["dates"]["start"]
        if dates.get("dateTime"):
            date_iso = dates["dateTime"]
        else:
            date_display = dates.get("localDate")
    except KeyError:
        pass

    segment = None
    genre = None
    try:
        classification = e["classifications"][0]
        segment = classification["segment"]["name"]
        genre = classification.get("genre", {}).get("name")
    except (KeyError, IndexError):
        pass
    category = genre or segment

    image_url = None
    if e.get("images"):
        image_url = max(e["images"], key=lambda i: i.get("width", 0)).get("url")

    price_display = None
    price_max = None
    if e.get("priceRanges"):
        pr = e["priceRanges"][0]
        price_display = f"${pr.get('min', '?')}–${pr.get('max', '?')}"
        price_max = pr.get("max")

    title = e.get("name")
    event_type = classify.classify_type(
        title=title, venue=venue, raw_category=category, source_segment=segment
    )
    scale = classify.classify_scale(
        price_max=price_max,
        major_price_threshold=cls_cfg.get("major_price_threshold", 150),
        midsize_price_threshold=cls_cfg.get("midsize_price_threshold", 50),
    )

    return make_event(
        title=title,
        venue=venue,
        date=date_iso,
        date_display=date_display,
        url=e.get("url"),
        category=category,
        source="Ticketmaster",
        image_url=image_url,
        price_display=price_display,
        event_type=event_type,
        scale=scale,
    )
