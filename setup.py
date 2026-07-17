#!/usr/bin/env python3
"""
setup.py
--------
Interactive wizard that asks for the four things that shape what shows
up on the board:

  1. Town name          -> geocoded to lat/lng automatically
  2. Travel time         -> converted to a search radius (see note below)
  3. Time frame           -> how far ahead to look for events, written as days_ahead
  4. Event types you want -> written as event_type_filter

Writes the results into config.yaml. Re-run any time to change your
answers — it only touches the fields it asks about, everything else in
config.yaml (API keys, feed URLs, custom sites) is left alone.

Usage:
    python3 setup.py [--config config.yaml]

Note on travel time: this is an approximation, not real driving-time
routing. It converts your answer into a straight-line search radius using
an assumed average road speed (default 30 mph, padded 1.3x since real
roads aren't straight lines) — the same kind of radius parameter
Ticketmaster and SeatGeek's own APIs expect. It'll be roughly right, not
turn-by-turn precise. Adjust avg_speed_mph in config.yaml if 30 mph is a
poor fit for your area (highway-heavy areas can go higher, dense urban
areas lower).
"""

import argparse

import yaml

import geocode
from classify import EVENT_TYPES

DEFAULT_AVG_SPEED_MPH = 30
PADDING_FACTOR = 1.3  # roads aren't straight lines

TIME_FRAME_PRESETS = [
    ("Next 2 weeks", 14),
    ("Next month", 30),
    ("Next 2 months", 60),
    ("Next 3 months", 90),
    ("Next 6 months", 180),
    ("Custom", None),
]


def load_config(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def save_config(path, config):
    with open(path, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)


def ask_town():
    while True:
        town = input("\nWhat town are you in? (e.g. 'Princeton, NJ'): ").strip()
        if not town:
            print("  Please enter a town.")
            continue
        try:
            lat, lng, display_name = geocode.geocode_town(town)
        except Exception as exc:
            print(f"  Couldn't find that: {exc}")
            continue

        confirm = input(f"  Found: {display_name}\n  Is that right? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            return town, lat, lng
        # loop and try again


def ask_travel_time():
    while True:
        raw = input(
            "\nHow far are you willing to travel, in minutes by car? "
            "(e.g. 20) [default 30]: "
        ).strip()
        if not raw:
            return 30
        try:
            minutes = int(raw)
            if minutes <= 0:
                raise ValueError
            return minutes
        except ValueError:
            print("  Please enter a positive number of minutes.")


def minutes_to_radius(minutes, avg_speed_mph=DEFAULT_AVG_SPEED_MPH):
    radius = (minutes / 60) * avg_speed_mph * PADDING_FACTOR
    return round(max(radius, 3), 1)  # sensible floor for very short travel times


def ask_time_frame():
    print("\nHow far ahead do you want to look for events?\n")
    for i, (label, days) in enumerate(TIME_FRAME_PRESETS, start=1):
        suffix = f" ({days} days)" if days else ""
        print(f"  {i}. {label}{suffix}")

    while True:
        raw = input("\nYour choice [default: Next 2 months]: ").strip()
        if not raw:
            return 60
        if not raw.isdigit() or not (1 <= int(raw) <= len(TIME_FRAME_PRESETS)):
            print(f"  Please enter a number from 1 to {len(TIME_FRAME_PRESETS)}.")
            continue

        label, days = TIME_FRAME_PRESETS[int(raw) - 1]
        if days is not None:
            return days

        # Custom — ask for a specific number of days
        while True:
            custom_raw = input("  How many days ahead? (e.g. 45): ").strip()
            try:
                custom_days = int(custom_raw)
                if custom_days <= 0:
                    raise ValueError
                return custom_days
            except ValueError:
                print("  Please enter a positive number of days.")


def ask_event_types():
    print("\nWhich kinds of events are you interested in?")
    print("  (Enter numbers separated by commas, or press Enter for all)\n")
    for i, event_type in enumerate(EVENT_TYPES, start=1):
        print(f"  {i}. {event_type}")

    raw = input("\nYour choices: ").strip()
    if not raw:
        return []  # empty filter = keep everything

    chosen = []
    for part in raw.split(","):
        part = part.strip()
        if not part.isdigit():
            continue
        idx = int(part) - 1
        if 0 <= idx < len(EVENT_TYPES):
            chosen.append(EVENT_TYPES[idx])

    if not chosen:
        print("  Didn't recognize any of those — keeping all event types.")
        return []
    return chosen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    print("=" * 50)
    print("Local Events Board — setup")
    print("=" * 50)

    config = load_config(args.config)

    town, lat, lng = ask_town()
    minutes = ask_travel_time()
    avg_speed = config.get("location", {}).get("avg_speed_mph", DEFAULT_AVG_SPEED_MPH)
    radius = minutes_to_radius(minutes, avg_speed)
    days_ahead = ask_time_frame()
    event_types = ask_event_types()

    config["location"] = {
        "name": town,
        "lat": lat,
        "lng": lng,
        "radius_miles": radius,
        "travel_minutes": minutes,
        "avg_speed_mph": avg_speed,
    }
    config["days_ahead"] = days_ahead
    config["event_type_filter"] = event_types

    save_config(args.config, config)

    print("\n" + "=" * 50)
    print(f"Saved to {args.config}:")
    print(f"  Location: {town} ({lat}, {lng})")
    print(f"  Travel time: {minutes} min -> ~{radius} mile search radius")
    print(f"  Time frame: next {days_ahead} days")
    print(f"  Event types: {', '.join(event_types) if event_types else 'All'}")
    print("=" * 50)
    print("\nRun python3 scraper.py to pull events with these settings.")


if __name__ == "__main__":
    main()
