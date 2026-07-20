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
from bs4 import BeautifulSoup

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
    county = feed.get("county")

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
        url = _event_url(comp, feed)

        date_iso, date_display, end_date_iso = _extract_date(comp)

        raw_category = feed.get("category", "Community")
        venue_name = location_text or feed["name"]
        # `event_type:` on the feed overrides the keyword guess entirely
        # — useful when every event from this feed is genuinely the same
        # type.
        event_type = feed.get("event_type") or classify.classify_type(
            title=title, venue=venue_name, raw_category=raw_category
        )
        # ICS feeds only exist here because they're school/library/town
        # calendars, not major ticketing platforms — Local-Community by
        # definition, no heuristic needed.
        scale = classify.classify_scale(is_school_or_community_source=True)
        price_display = _price_display(comp, feed, url)

        events.append(
            make_event(
                title=title,
                venue=venue_name,
                date=date_iso,
                date_display=date_display,
                end_date=end_date_iso,
                url=url,
                category=raw_category,
                source=feed["name"],
                event_type=event_type,
                scale=scale,
                county=county,
                price_display=price_display,
            )
        )
    return events


def _event_url(comp, feed):
    """
    Link shown on the dashboard card for this specific event, in order
    of preference:
      1. The VEVENT's own URL property, if the feed sets one (most ICS
         feeds don't).
      2. `event_url_template` from config, if set — a per-feed URL
         pattern with a `{uid}` placeholder, substituted with this
         event's ICS UID. Some calendar platforms (e.g. Communico/
         libnet.info, used by many public libraries) give every event a
         real page at a predictable URL like
         "https://yourlibrary.libnet.info/event/{uid}" even though the
         feed itself never fills in URL: — check the feed's own event
         pages for a UID-shaped number in the address bar to see if this
         applies.
      3. `page_url` (or the feed URL itself as a last resort) — a
         general "browse all events here" link, when there's no way to
         deep-link to this specific event.
    """
    if comp.get("url"):
        return str(comp.get("url"))

    template = feed.get("event_url_template")
    uid = comp.get("uid")
    if template and uid:
        return template.replace("{uid}", str(uid))

    return feed.get("page_url", feed["url"])


def _price_display(comp, feed, url):
    """
    Standard ICS has no price field. When `fetch_price_from_page` is set
    on the feed, fetches the event's own page and reads its cost widget
    for an exact price like "$85" — a pattern used by The Events
    Calendar's Elementor integration (e.g. Arts Council of Princeton).
    One extra HTTP request per event, so it's opt-in per feed rather
    than automatic. Falls back to "Free" when the feed's own "Free or
    Low Cost" category tag is the only signal available (or the page
    fetch didn't find a price), and blank otherwise — an absent tag
    doesn't confirm an event actually costs money.
    """
    if feed.get("fetch_price_from_page") and url and url != feed.get("page_url"):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            cost_el = soup.select_one(".tec-events-elementor-event-widget__cost")
            if cost_el:
                text = cost_el.get_text(strip=True)
                if text:
                    return text
        except Exception as exc:
            print(f"    [ics_calendar] price fetch failed for {url}: {exc}")

    categories = comp.get("categories")
    if categories and "free or low cost" in str(categories).lower():
        return "Free"
    return None


def _extract_date(comp):
    """
    Returns (date_iso, date_display, end_date_iso). end_date_iso is only
    set for a genuinely multi-day all-day event (an exhibition, a
    festival) — the last day it actually runs, not just its opening day.
    """
    dtstart = comp.get("dtstart")
    if dtstart is None:
        return None, None, None
    value = dtstart.dt
    if isinstance(value, datetime):
        return value.isoformat(), None, None
    if isinstance(value, date):
        # All-day event, no time component. Anchor on local midnight —
        # an explicit T00:00:00, not a bare date string — so the
        # dashboard parses it as local time rather than UTC (the same
        # day-rollover trap fixed for isoDate() there).
        date_iso = f"{value.isoformat()}T00:00:00"

        end_date_iso = None
        dtend = comp.get("dtend")
        if dtend is not None:
            end_value = dtend.dt
            if isinstance(end_value, date) and not isinstance(end_value, datetime):
                # DTEND on an all-day event is exclusive per RFC 5545 —
                # it's the day *after* the last full day, so the actual
                # last day is one day earlier.
                last_day = end_value - timedelta(days=1)
                if last_day > value:
                    end_date_iso = f"{last_day.isoformat()}T00:00:00"

        return date_iso, None, end_date_iso
    return None, str(value), None
