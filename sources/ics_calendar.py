"""
ics_calendar.py
----------------
Pulls events from standard iCalendar (.ics) feeds — the single best way
to catch genuinely local, small-scale events that never show up on
Ticketmaster or SeatGeek: school plays, PTA meetings, township rec
programs, library storytimes, etc.

Almost every school district site, municipal site, and library site has
one of these, even if it's not obvious. Look for:
  - A "Calendar RSS Feeds", "Subscribe", or calendar-icon link near any
    calendar widget (common on Finalsite-powered school sites — very
    common for US public schools/districts).
  - An "Add to Calendar" / RSS button on the events page itself (common
    on Communico/libnet.info-powered library sites — these are often
    JS-rendered single-page apps with no server-rendered event HTML, so
    custom_html scraping won't work on them, but the built-in calendar
    feed does).
  - A public Google Calendar "Secret address in iCal format" (Calendar
    Settings -> Integrate calendar, on calendars set to public).
  - A "Subscribe" / "Export" / "ICS" button on library or rec department
    event pages (common on LibCal, Rec Desk, and similar platforms).

This is far more durable than HTML scraping: the feed format is
standardized, so it doesn't break when a site gets redesigned.

Recurring events (RRULE, e.g. a weekly meeting) are expanded correctly
using the recurring_ical_events library.
"""

from datetime import datetime, timedelta, date

import icalendar
import recurring_ical_events
import requests

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
    src_cfg = config["sources"].get("ics_calendars", {})
    feeds = src_cfg.get("feeds", [])
    days_ahead = config.get("days_ahead", 60)

    events = []
    for feed in feeds:
        name = feed.get("name", "?")
        if not feed.get("url") or "PASTE_" in feed["url"]:
            print(f"  [ics_calendar] {name}: skipped — no feed URL set in config.yaml")
            runlog.record(name, status="not_configured", error="No feed URL set in config.yaml — this entry is still a placeholder")
            continue
        try:
            feed_events = _fetch_feed(feed, days_ahead)
            print(f"  [ics_calendar] {name}: {len(feed_events)} events")
            events.extend(feed_events)
            if feed_events:
                runlog.record(name, status="ok", count=len(feed_events))
            else:
                runlog.record(
                    name, status="empty",
                    detail="Feed loaded but had no events in the date window — could be genuinely quiet, or the wrong calendar was subscribed",
                )
        except Exception as exc:
            print(f"  [ics_calendar] {name} failed: {exc}")
            runlog.record(name, status="error", error=str(exc))

    return events


def _fetch_feed(feed, days_ahead):
    resp = requests.get(feed["url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()

    cal = icalendar.Calendar.from_ical(resp.text)

    start = datetime.now()
    end = start + timedelta(days=days_ahead)
    occurrences = recurring_ical_events.of(cal).between(start, end)

    events = []
    for comp in occurrences:
        title = str(comp.get("summary", "Untitled event"))
        location_text = str(comp.get("location")) if comp.get("location") else None
        url = str(comp.get("url")) if comp.get("url") else feed.get("page_url", feed["url"])

        date_iso, date_display = _extract_date(comp)

        raw_category = feed.get("category", "Community")
        venue_name = location_text or feed["name"]
        event_type = classify.classify_type(
            title=title, venue=venue_name, raw_category=raw_category
        )
        # ICS feeds only exist here because they're school/library/town
        # calendars, not major ticketing platforms — Local-Community by
        # definition, no heuristic needed.
        scale = classify.classify_scale(is_school_or_community_source=True)

        events.append(
            make_event(
                title=title,
                venue=venue_name,
                date=date_iso,
                date_display=date_display,
                url=url,
                category=raw_category,
                source=feed["name"],
                event_type=event_type,
                scale=scale,
            )
        )
    return events


def _extract_date(comp):
    dtstart = comp.get("dtstart")
    if dtstart is None:
        return None, None
    value = dtstart.dt
    if isinstance(value, datetime):
        return value.isoformat(), None
    if isinstance(value, date):
        # all-day event, no time component
        return None, f"{value.strftime('%B')} {value.day}, {value.year}"
    return None, str(value)
