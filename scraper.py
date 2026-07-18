#!/usr/bin/env python3
"""
scraper.py
----------
Run this daily (via cron, or via the GitHub Actions workflow in
.github/workflows/update-events.yml) to refresh the event dashboard.

Usage:
    python3 scraper.py [--config config.yaml]

What it does:
    1. Loads config.yaml (API keys can be overridden by the
       TICKETMASTER_API_KEY / SEATGEEK_CLIENT_ID environment variables —
       this is how GitHub Actions supplies them from repository secrets
       without ever putting real keys in a public config.yaml)
    2. Pulls events from every enabled source (Ticketmaster, SeatGeek,
       ICS calendar feeds, custom HTML scrapes, manual quick-adds)
    3. De-duplicates overlapping events across sources
    4. Upserts everything into a local SQLite database (data/events.db)
    5. Exports a fresh dashboard/events.json for the static dashboard
    6. Prunes events whose date has already passed
"""

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from normalize import EVENT_FIELDS, dedupe
from sources import ticketmaster, seatgeek, custom_html, ics_calendar, manual
import runlog

SOURCE_MODULES = {
    "ticketmaster": ticketmaster,
    "seatgeek": seatgeek,
    "ics_calendars": ics_calendar,
    "custom_html": custom_html,
    "manual": manual,
}


def load_config(path):
    with open(path, "r") as f:
        config = yaml.safe_load(f)

    # Environment variables win over whatever's in config.yaml. This is
    # what lets GitHub Actions inject real keys from repository secrets
    # at run time, so a public repo's config.yaml never needs to contain
    # actual credentials.
    env_overrides = {
        "ticketmaster": os.environ.get("TICKETMASTER_API_KEY"),
        "seatgeek_client_id": os.environ.get("SEATGEEK_CLIENT_ID"),
    }
    for key, value in env_overrides.items():
        if value:
            config.setdefault("api_keys", {})[key] = value

    _merge_local_sources(config)
    _merge_sheet_sources(config)

    return config


def _merge_local_sources(config, path="local_sources.yaml"):
    """
    local_sources.yaml is the maintainer-facing "add a place" file —
    kept separate from config.yaml so adding a venue is a small, low-risk
    edit. Its ics_calendars/custom_html lists get appended onto the same
    lists in config.yaml so sources/ics_calendar.py and
    sources/custom_html.py don't need to know two files exist.
    """
    if not os.path.exists(path):
        return

    with open(path, "r") as f:
        local = yaml.safe_load(f) or {}

    extra_feeds = local.get("ics_calendars") or []
    if extra_feeds:
        config.setdefault("sources", {}).setdefault("ics_calendars", {}).setdefault("feeds", [])
        config["sources"]["ics_calendars"]["feeds"].extend(extra_feeds)

    extra_sites = local.get("custom_html") or []
    if extra_sites:
        config.setdefault("sources", {}).setdefault("custom_html", {}).setdefault("sites", [])
        config["sources"]["custom_html"]["sites"].extend(extra_sites)


# Required on every sheet row, regardless of type.
_SHEET_REQUIRED_COLS = ("type", "name", "url")


def _merge_sheet_sources(config):
    """
    Optional spreadsheet-friendly alternative to local_sources.yaml: set
    `sheet_url` in config.yaml to a Google Sheet published to the web as
    CSV (File -> Share -> Publish to web -> CSV), and each row becomes an
    ics_calendars feed or custom_html site, same as if it'd been added to
    local_sources.yaml by hand. Lets a non-technical maintainer manage
    sources in a spreadsheet instead of editing YAML.

    A row's `type` column picks which shape it becomes:
      type=ics          -> name, url, page_url, event_url_template,
                            category, county
      type=custom_html   -> name, url, event_selector, title_selector,
                            date_selector, link_selector, link_attr,
                            scale, county
    Unset optional columns just fall back to the same defaults the YAML
    versions use. Blank/missing `type`, `name`, or `url` skips that row.
    """
    sheet_url = config.get("sheet_url")
    if not sheet_url:
        return

    # A malformed sheet (bad URL, ragged CSV, a row with the wrong number
    # of columns) should never take down the whole scraper run — same
    # "one bad source doesn't break the others" contract every other
    # source module follows.
    try:
        resp = requests.get(sheet_url, timeout=20)
        resp.raise_for_status()
        # Google's CSV export doesn't add one in practice, but strip a
        # BOM defensively in case a different export path does — left
        # in place it would corrupt the first column's header name.
        text = resp.text.lstrip("﻿")
        rows = list(csv.DictReader(io.StringIO(text)))

        feeds = []
        sites = []
        skipped = 0
        for row in rows:
            # Column headers are matched case-insensitively — "Type" and
            # "type" both work, since capitalizing header row labels is
            # the natural thing to do in a spreadsheet.
            row = {(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
            if not all(row.get(col) for col in _SHEET_REQUIRED_COLS):
                skipped += 1
                continue

            row_type = row["type"].lower()
            county = row.get("county") or None

            if row_type == "ics":
                feeds.append({
                    "name": row["name"],
                    "url": row["url"],
                    "page_url": row.get("page_url") or row["url"],
                    "category": row.get("category") or "Community",
                    "county": county,
                    "event_url_template": row.get("event_url_template") or None,
                })
            elif row_type == "custom_html":
                sites.append({
                    "name": row["name"],
                    "url": row["url"],
                    "event_selector": row.get("event_selector"),
                    "title_selector": row.get("title_selector"),
                    "date_selector": row.get("date_selector"),
                    "link_selector": row.get("link_selector") or "a",
                    "link_attr": row.get("link_attr") or "href",
                    "scale": row.get("scale") or "Mid-size",
                    "county": county,
                })
            else:
                print(f"  [sheet] row {row['name']!r} has type {row['type']!r} — must be 'ics' or 'custom_html', skipping")
                skipped += 1

        if feeds:
            config.setdefault("sources", {}).setdefault("ics_calendars", {}).setdefault("feeds", [])
            config["sources"]["ics_calendars"]["feeds"].extend(feeds)
        if sites:
            config.setdefault("sources", {}).setdefault("custom_html", {}).setdefault("sites", [])
            config["sources"]["custom_html"]["sites"].extend(sites)

        total = len(feeds) + len(sites)
        print(f"  [sheet] loaded {total} source(s) from Google Sheet ({len(feeds)} ICS feed(s), {len(sites)} custom_html site(s)), {skipped} row(s) skipped")
        runlog.record("Google Sheet sources", status="ok" if total else "empty", count=total)
    except Exception as exc:
        print(f"  [sheet] couldn't load sources from sheet_url ({exc}) — skipping")
        runlog.record("Google Sheet sources", status="error", error=str(exc))


def run_sources(config):
    all_events = []
    for name, module in SOURCE_MODULES.items():
        src_cfg = config["sources"].get(name, {})
        if not src_cfg.get("enabled", False):
            runlog.record(name, status="disabled", detail="enabled: false in config.yaml")
            continue
        print(f"Running source: {name}")
        try:
            events = module.fetch(config)
            all_events.extend(events)
        except Exception as exc:
            print(f"  [{name}] source failed entirely: {exc}")
            runlog.record(name, status="error", error=str(exc))
    return all_events


def apply_keyword_filter(events, keywords):
    if not keywords:
        return events
    keywords = [k.lower() for k in keywords]
    filtered = []
    for e in events:
        haystack = " ".join(
            str(e.get(f, "")) for f in ("title", "category", "venue")
        ).lower()
        if any(k in haystack for k in keywords):
            filtered.append(e)
    return filtered


def apply_event_type_filter(events, allowed_types):
    """Keeps only events whose event_type is in allowed_types. Empty list
    (the default — no filter set, or set via setup.py by pressing Enter
    for "all") keeps everything."""
    if not allowed_types:
        return events
    allowed = set(allowed_types)
    return [e for e in events if e.get("event_type") in allowed]


def ensure_db(db_path):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    other_cols = ", ".join(f"{f} TEXT" for f in EVENT_FIELDS if f != "id")
    conn.execute(f"CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, {other_cols})")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_status (
            name TEXT PRIMARY KEY,
            status TEXT,
            count INTEGER,
            error TEXT,
            detail TEXT,
            checked_at TEXT
        )
    """)
    conn.commit()

    # CREATE TABLE IF NOT EXISTS doesn't add columns to a database left
    # over from before a field was added to EVENT_FIELDS (e.g. county) —
    # patch those in so upserts against an older data/events.db don't
    # fail with "no such column".
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    for field in EVENT_FIELDS:
        if field != "id" and field not in existing_cols:
            conn.execute(f"ALTER TABLE events ADD COLUMN {field} TEXT")
    conn.commit()

    return conn


def upsert_events(conn, events):
    placeholders = ", ".join("?" for _ in EVENT_FIELDS)
    cols = ", ".join(EVENT_FIELDS)
    updates = ", ".join(f"{f}=excluded.{f}" for f in EVENT_FIELDS if f != "id")
    sql = f"""
        INSERT INTO events ({cols}) VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {updates}
    """
    rows = [tuple(e.get(f) for f in EVENT_FIELDS) for e in events]
    conn.executemany(sql, rows)
    conn.commit()


def prune_past_events(conn):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "DELETE FROM events WHERE date IS NOT NULL AND substr(date,1,10) < ?", (today,)
    )
    conn.commit()


def export_json(conn, export_path):
    cur = conn.execute(
        f"SELECT {', '.join(EVENT_FIELDS)} FROM events "
        "ORDER BY (date IS NULL), date ASC"
    )
    rows = [dict(zip(EVENT_FIELDS, row)) for row in cur.fetchall()]

    os.makedirs(os.path.dirname(export_path) or ".", exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "events": rows,
    }
    with open(export_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Exported {len(rows)} events to {export_path}")


def upsert_source_status(conn, entries, checked_at):
    sql = """
        INSERT INTO source_status (name, status, count, error, detail, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            status=excluded.status, count=excluded.count, error=excluded.error,
            detail=excluded.detail, checked_at=excluded.checked_at
    """
    rows = [
        (e["name"], e["status"], e.get("count", 0), e.get("error"), e.get("detail"), checked_at)
        for e in entries
    ]
    conn.executemany(sql, rows)
    conn.commit()


# Sort order so the things most worth your attention float to the top:
# broken/missing sources first, then quiet ones, then healthy ones.
_STATUS_SORT_ORDER = {"error": 0, "not_configured": 1, "empty": 2, "disabled": 3, "ok": 4}


def export_status_json(conn, path):
    cur = conn.execute("SELECT name, status, count, error, detail, checked_at FROM source_status")
    rows = [
        dict(zip(["name", "status", "count", "error", "detail", "checked_at"], row))
        for row in cur.fetchall()
    ]
    rows.sort(key=lambda r: (_STATUS_SORT_ORDER.get(r["status"], 9), r["name"]))

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": rows,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Exported source status ({len(rows)} sources) to {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)

    runlog.reset()
    raw_events = run_sources(config)
    print(f"\nTotal raw events pulled: {len(raw_events)}")

    filtered = apply_keyword_filter(raw_events, config.get("keyword_filter"))
    filtered = apply_event_type_filter(filtered, config.get("event_type_filter"))
    deduped = dedupe(filtered)
    print(f"After filtering + dedup: {len(deduped)}")

    conn = ensure_db(config["database_path"])
    upsert_events(conn, deduped)
    prune_past_events(conn)
    export_json(conn, config["export_json_path"])

    checked_at = datetime.now(timezone.utc).isoformat()
    upsert_source_status(conn, runlog.get_entries(), checked_at)
    status_path = config.get("source_status_path", "dashboard/sources_status.json")
    export_status_json(conn, status_path)

    conn.close()

    print("\nDone. Open dashboard/index.html (via a local server) to view results.")
    print(f"Source status: open dashboard/status.html to see what was checked and what's missing/broken.")


if __name__ == "__main__":
    main()
