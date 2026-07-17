"""
Custom HTML source.
------------------
Generic, config-driven scraper for local venues/calendars that don't
have a public API or ICS feed. Each site in config.yaml ->
sources.custom_html.sites just needs CSS selectors pointing at the
repeating event card, and the title/date/link within it.

NOTE: these selectors WILL break when a site redesigns. That's normal —
when a site stops returning events, inspect the page (right-click ->
Inspect) and update the selectors for that one entry. Nothing else in
the pipeline needs to change. Prefer sources/ics_calendar.py over this
whenever a site offers a calendar feed — it's far more durable.
"""

import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
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
    src_cfg = config["sources"].get("custom_html", {})
    sites = src_cfg.get("sites", [])
    events = []

    for site in sites:
        try:
            site_events = _scrape_site(site)
            print(f"  [custom_html] {site['name']}: {len(site_events)} events")
            events.extend(site_events)
            if site_events:
                runlog.record(site["name"], status="ok", count=len(site_events))
            else:
                runlog.record(
                    site["name"], status="empty",
                    detail="Page loaded but selectors matched 0 events — likely stale selectors or no upcoming events listed",
                )
        except Exception as exc:
            print(f"  [custom_html] {site['name']} failed: {exc}")
            runlog.record(site["name"], status="error", error=str(exc))

    return events


def _scrape_site(site):
    resp = requests.get(site["url"], headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    cards = soup.select(site["event_selector"])
    events = []
    for card in cards:
        title_el = card.select_one(site["title_selector"])
        date_el = card.select_one(site["date_selector"])
        link_el = card.select_one(site.get("link_selector", "a"))

        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        raw_date = date_el.get_text(strip=True) if date_el else None

        date_iso, date_display = _parse_date(raw_date)

        link = None
        if link_el and link_el.has_attr(site.get("link_attr", "href")):
            link = urljoin(site["url"], link_el[site.get("link_attr", "href")])

        category = site.get("category", "Local Venue")
        event_type = classify.classify_type(
            title=title, venue=site["name"], raw_category=category
        )
        # Venues in this list range from a town library to a regional
        # performing-arts center that hosts touring acts, so scale isn't
        # automatically "Local-Community" the way it is for school/town
        # ICS feeds. Set `scale:` per site in config.yaml (Major /
        # Mid-size / Local-Community) if you know it; defaults to
        # Mid-size, a reasonable middle ground for a venue with its own
        # events page but no popularity signal available.
        scale = site.get("scale", "Mid-size")

        events.append(
            make_event(
                title=title,
                venue=site["name"],
                date=date_iso,
                date_display=date_display or raw_date,
                url=link or site["url"],
                category=category,
                source=site["name"],
                event_type=event_type,
                scale=scale,
            )
        )
    return events


def _parse_date(raw_date):
    if not raw_date:
        return None, None
    try:
        dt = dateparser.parse(raw_date, fuzzy=True)
        return dt.isoformat(), None
    except (ValueError, OverflowError):
        return None, raw_date
