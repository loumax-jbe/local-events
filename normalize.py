"""
normalize.py
------------
Defines the common event schema every source maps into, plus a
de-duplication helper (events from Ticketmaster + SeatGeek + custom
scrapes often overlap for big shows at the same venue).
"""

import hashlib
import re


EVENT_FIELDS = [
    "id",           # stable hash, used as primary key
    "title",
    "venue",
    "date",         # ISO 8601, e.g. 2026-08-14T19:30:00
    "date_display", # human-readable fallback if time unknown
    "url",
    "category",     # raw label from the source (genre, feed category, etc.)
    "source",
    "image_url",
    "price_display",
    "event_type",   # Concert, Theater & Performing Arts, School & Youth, etc. See classify.py
    "scale",         # Major / Mid-size / Local-Community — see classify.py
    "county",         # which county this venue is in — powers the "What's Going On In Our Area" county filter
]


def make_event(**kwargs):
    """Build a normalized event dict, filling missing fields with None."""
    event = {field: kwargs.get(field) for field in EVENT_FIELDS}
    event["id"] = event["id"] or _make_id(event["title"], event["venue"], event["date"])
    return event


def _make_id(title, venue, date):
    key = f"{_clean(title)}|{_clean(venue)}|{_clean(date)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _clean(s):
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip().lower()


def dedupe(events):
    """
    Collapse near-duplicate events (same title + venue + date, even if
    the IDs differ because they came from different sources).
    """
    seen = {}
    for e in events:
        key = (_clean(e.get("title")), _clean(e.get("venue")), _clean(e.get("date"))[:10])
        if key not in seen:
            seen[key] = e
        else:
            # Prefer the entry that has more complete info (image, price, etc.)
            existing = seen[key]
            existing_score = sum(1 for f in EVENT_FIELDS if existing.get(f))
            new_score = sum(1 for f in EVENT_FIELDS if e.get(f))
            if new_score > existing_score:
                seen[key] = e
    return list(seen.values())
