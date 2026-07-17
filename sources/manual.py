"""
manual.py
---------
Reads manual_events.yaml — hand-entered events for things you hear about
off-platform (Instagram posts, flyers, word of mouth) that no API or
calendar feed will ever surface. These flow through the exact same
dedup/classification/storage pipeline as every automated source.
"""

import os
import yaml

from normalize import make_event
import classify
import runlog

DEFAULT_PATH = "manual_events.yaml"


def fetch(config):
    path = config.get("manual_events_path", DEFAULT_PATH)
    if not os.path.exists(path):
        runlog.record("Manual quick-add", status="not_configured", error=f"{path} not found")
        return []

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    raw_events = data.get("events", [])
    events = []
    for raw in raw_events:
        title = raw.get("title")
        if not title:
            continue

        venue = raw.get("venue")
        category = raw.get("category")

        event_type = raw.get("event_type") or classify.classify_type(
            title=title, venue=venue, raw_category=category
        )
        # Manual entries are, by construction, things you personally
        # heard about locally — Local-Community unless you say otherwise.
        scale = raw.get("scale") or classify.classify_scale(
            is_school_or_community_source=True
        )

        source_note = raw.get("source_note", "Manually added")

        events.append(
            make_event(
                title=title,
                venue=venue,
                date=raw.get("date"),
                date_display=raw.get("date_display"),
                url=raw.get("url") or None,
                category=category,
                source=source_note,
                price_display=raw.get("price_display") or None,
                event_type=event_type,
                scale=scale,
            )
        )

    print(f"  [manual] loaded {len(events)} events from {path}")
    if events:
        runlog.record("Manual quick-add", status="ok", count=len(events))
    else:
        runlog.record("Manual quick-add", status="empty", detail=f"{path} has no entries yet")
    return events
