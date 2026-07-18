"""
events_sheet.py
----------------
Reads ready-made events directly from a Google Sheet published to the
web as CSV — the live-data equivalent of manual_events.yaml. Useful for
sources that publish their own events as a spreadsheet rather than an
ICS feed or a scrapable web page (school district activity calendars
often work this way).

Expected columns (case-insensitive, extras ignored): title (or
"event"), date, time, venue (or "location"), category (or "school"),
event_type, scale, price_display (or "admission"), source_note, county,
url. Only title and date are required — same minimal bar as
manual_events.yaml.
"""

import csv
import io

import requests
from dateutil import parser as dateparser

from normalize import make_event
import classify
import runlog

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; local-event-aggregator/1.0; "
        "personal use, low request volume)"
    )
}


def fetch(config):
    sheet_url = config.get("events_sheet_url")
    if not sheet_url:
        runlog.record("Events sheet", status="not_configured", error="No events_sheet_url set in config.yaml")
        return []

    try:
        resp = requests.get(sheet_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        text = resp.text.lstrip("﻿")
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:
        print(f"  [events_sheet] couldn't load events_sheet_url ({exc})")
        runlog.record("Events sheet", status="error", error=str(exc))
        return []

    events = []
    skipped = 0
    for raw in rows:
        row = {(k or "").strip().lower(): str(v or "").strip() for k, v in raw.items()}
        title = row.get("title") or row.get("event")
        raw_date = row.get("date")
        if not title or not raw_date:
            skipped += 1
            continue

        date_iso, date_display = _parse_datetime(raw_date, row.get("time"))

        venue = row.get("venue") or row.get("location") or None
        category = row.get("category") or row.get("school") or None
        event_type = row.get("event_type") or classify.classify_type(
            title=title, venue=venue, raw_category=category
        )
        scale = row.get("scale") or classify.classify_scale(is_school_or_community_source=True)
        price_display = row.get("price_display") or row.get("admission") or None
        source_note = row.get("source_note") or row.get("school") or "Events sheet"
        # Sheets you don't control (e.g. a school district's own list)
        # often won't have a County column at all — events_sheet_county
        # in config.yaml covers that whole sheet at once, since a
        # per-row value obviously isn't available to add there.
        county = row.get("county") or config.get("events_sheet_county") or None

        events.append(
            make_event(
                title=title,
                venue=venue,
                date=date_iso,
                date_display=date_display,
                url=row.get("url") or None,
                category=category,
                source=source_note,
                price_display=price_display,
                event_type=event_type,
                scale=scale,
                county=county,
            )
        )

    print(f"  [events_sheet] loaded {len(events)} events, {skipped} row(s) skipped")
    if events:
        runlog.record("Events sheet", status="ok", count=len(events))
    else:
        runlog.record("Events sheet", status="empty", detail="Sheet loaded but had no usable rows")
    return events


def _parse_datetime(raw_date, raw_time):
    """
    Combines Date + Time into a full timestamp when both are present and
    parse cleanly. Falls back to the parsed date with no time (still a
    real, sortable/filterable date — just date_display is a display-only
    fallback for when even the date itself doesn't parse) when Time is
    missing, "TBD", or otherwise not parseable.
    """
    try:
        date_only = dateparser.parse(raw_date, fuzzy=True)
    except (ValueError, OverflowError):
        return None, raw_date

    if raw_time:
        try:
            combined = dateparser.parse(raw_time, default=date_only, fuzzy=True)
            return combined.isoformat(), None
        except (ValueError, OverflowError):
            pass

    return date_only.isoformat(), None
