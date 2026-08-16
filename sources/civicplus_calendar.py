"""
civicplus_calendar.py
----------------------
Pulls events from a CivicPlus-powered town/municipal site's calendar RSS
feed — a common government website platform (montgomerynj.gov is one
example) whose calendar module exposes a per-calendar RSS feed at:

    /RSSFeed.aspx?ModID=58&CID=<calendar-slug-id>

Find the URL for a given calendar by visiting the site's /rss.aspx page
and looking under the "Calendar" section — most CivicPlus sites list
every calendar there, including the "All" combined feed and each
individual department/committee calendar.

Most calendars on a town site are internal board/committee meetings
(Planning Board, Zoning, Finance & Tax, etc.), not public events — pick
the ones that are actually community-facing (a "Main Calendar", "Parks
& Recreation", etc.) rather than adding all of them.

The feed's items use a custom `calendarEvent:` XML namespace for
EventDates/EventTimes/Location — parsed here by local tag name only
(ignoring the namespace URI), since that URI is tied to each town's own
domain and isn't consistent across different CivicPlus sites.
"""

from xml.etree import ElementTree as ET

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
    src_cfg = config["sources"].get("civicplus_calendars", {})
    feeds = src_cfg.get("feeds", [])

    events = []
    for feed in feeds:
        name = feed.get("name", "?")
        if not feed.get("url"):
            print(f"  [civicplus_calendar] {name}: skipped — no feed URL set")
            runlog.record(name, status="not_configured", error="No feed URL set")
            continue
        try:
            feed_events = _fetch_feed(feed)
            print(f"  [civicplus_calendar] {name}: {len(feed_events)} events")
            events.extend(feed_events)
            if feed_events:
                runlog.record(name, status="ok", count=len(feed_events))
            else:
                runlog.record(
                    name, status="empty",
                    detail="Feed loaded but had no upcoming events — could be genuinely quiet right now",
                )
        except Exception as exc:
            print(f"  [civicplus_calendar] {name} failed: {exc}")
            runlog.record(name, status="error", error=str(exc))

    return events


def _local_tag(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def _fetch_feed(feed):
    county = feed.get("county")

    resp = requests.get(feed["url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else []

    events = []
    for item in items:
        fields = {}
        for child in item:
            fields.setdefault(_local_tag(child.tag), (child.text or "").strip())

        title = fields.get("title") or "Untitled event"
        link = fields.get("link")
        location = fields.get("Location")

        date_iso, date_display = _parse_date(
            fields.get("EventDates"), fields.get("EventTimes")
        )

        raw_category = feed.get("category", "Community")
        venue_name = location or feed["name"]
        # `event_type:` on the feed overrides the keyword guess entirely
        # — useful when every event from this calendar is genuinely the
        # same type.
        event_type = feed.get("event_type") or classify.classify_type(
            title=title, venue=venue_name, raw_category=raw_category
        )
        # Town calendars only exist here because they're municipal/
        # community programs, not major ticketing platforms —
        # Local-Community by definition, no heuristic needed.
        scale = classify.classify_scale(is_school_or_community_source=True)

        events.append(
            make_event(
                title=title,
                venue=venue_name,
                date=date_iso,
                date_display=date_display,
                url=link or feed.get("page_url") or feed["url"],
                category=raw_category,
                source=feed["name"],
                event_type=event_type,
                scale=scale,
                county=county,
            )
        )
    return events


def _parse_date(event_dates, event_times):
    """
    EventDates looks like "August 20, 2026". EventTimes looks like
    "07:00 PM - 11:00 PM" (start - end) or is blank for an all-day/
    unspecified-time listing — only the start time is used, matching
    how every other source here handles a start/end pair.
    """
    if not event_dates:
        return None, None
    try:
        date_only = dateparser.parse(event_dates, fuzzy=True)
    except (ValueError, OverflowError):
        return None, event_dates

    if event_times:
        start_time = event_times.split("-")[0].strip()
        try:
            combined = dateparser.parse(start_time, default=date_only, fuzzy=True)
            return combined.isoformat(), None
        except (ValueError, OverflowError):
            pass

    return date_only.isoformat(), None
