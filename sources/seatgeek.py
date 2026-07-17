"""
SeatGeek API source.
Docs: https://platform.seatgeek.com/
Free, instant client_id — no approval wait, no client_secret needed for
read-only event search.
"""

import requests
from datetime import datetime, timedelta
from normalize import make_event
import classify
import runlog

BASE_URL = "https://api.seatgeek.com/2/events"


def fetch(config):
    client_id = config["api_keys"].get("seatgeek_client_id", "")
    if not client_id or "YOUR_" in client_id:
        print("  [seatgeek] skipped — no client_id set in config.yaml")
        runlog.record("SeatGeek", status="not_configured", error="No client_id set in config.yaml")
        return []

    loc = config["location"]
    end_date = (datetime.utcnow() + timedelta(days=config.get("days_ahead", 60))).strftime(
        "%Y-%m-%d"
    )

    events = []
    page = 1
    per_page = 100
    request_error = None
    while True:
        params = {
            "client_id": client_id,
            "lat": loc["lat"],
            "lon": loc["lng"],
            "range": f"{loc.get('radius_miles', 25)}mi",
            "datetime_local.lte": end_date,
            "per_page": per_page,
            "page": page,
            "sort": "datetime_local.asc",
        }
        try:
            resp = requests.get(BASE_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            print(f"  [seatgeek] request failed: {exc}")
            request_error = str(exc)
            break

        page_events = data.get("events", [])
        if not page_events:
            break

        for e in page_events:
            events.append(_to_event(e))

        total = data.get("meta", {}).get("total", 0)
        if page * per_page >= total:
            break
        page += 1

    print(f"  [seatgeek] fetched {len(events)} events")
    if request_error:
        runlog.record("SeatGeek", status="error", count=len(events), error=request_error)
    elif not events:
        runlog.record("SeatGeek", status="empty")
    else:
        runlog.record("SeatGeek", status="ok", count=len(events))
    return events


def _to_event(e):
    venue = e.get("venue", {}).get("name")

    performers = e.get("performers", [])
    image_url = performers[0].get("image") if performers else None

    price_display = None
    stats = e.get("stats", {})
    if stats.get("lowest_price"):
        price_display = f"${stats['lowest_price']}+"

    category = e.get("type", "").replace("_", " ").title() or None
    title = e.get("title") or e.get("short_title")

    # SeatGeek's "score" (0-1) is a relative popularity signal across all
    # their listings — the best available proxy for "how big a deal is
    # this" since there's no attendance/capacity figure in the API.
    score = e.get("score")

    event_type = classify.classify_type(title=title, venue=venue, raw_category=category)
    scale = classify.classify_scale(seatgeek_score=score)

    return make_event(
        title=title,
        venue=venue,
        date=e.get("datetime_local"),
        url=e.get("url"),
        category=category,
        source="SeatGeek",
        image_url=image_url,
        price_display=price_display,
        event_type=event_type,
        scale=scale,
    )
