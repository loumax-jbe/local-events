"""
classify.py
-----------
Assigns two independent labels to every event, regardless of source:

  event_type — WHAT it is: Concert, Theater & Performing Arts, Comedy,
               Family & Kids, Festival & Fair, Sports, Community & Civic,
               School & Youth, Film, Other.

  scale      — HOW BIG it is: Major, Mid-size, Local-Community.
               This is the axis that actually separates "Alicia Keys at
               an arena" from "library concert" — two events can share
               an event_type (both "Concert") but sit at opposite ends
               of scale.

Each source maps its own raw category/genre data through here rather
than inventing its own labels, so the dashboard's filters mean the same
thing no matter where an event came from.
"""

import re

EVENT_TYPES = [
    "Concert",
    "Theater & Performing Arts",
    "Comedy",
    "Family & Kids",
    "Festival & Fair",
    "Sports",
    "Community & Civic",
    "School & Youth",
    "Film",
    "Other",
]

SCALES = ["Major", "Mid-size", "Local-Community"]

# Keyword -> event_type. Checked against title + venue + raw source category,
# in order, first match wins. Order matters: more specific terms first.
_TYPE_KEYWORDS = [
    ("School & Youth", [
        "elementary", "middle school", "high school", "pto", "ptsa", "pta",
        " jr.", " jr ", "student production", "school district", "k-12",
    ]),
    ("Family & Kids", [
        "storytime", "story time", "kids", "children's", "family day",
        "puppet", "petting zoo", "trick-or-treat",
    ]),
    ("Comedy", ["comedy", "stand-up", "standup", "open mic comedy"]),
    ("Festival & Fair", [
        "festival", "fair", "farmers market", "craft fair", "street fair",
        "carnival",
    ]),
    ("Sports", [
        "vs.", "vs ", "game day", "tournament", "5k", "10k", "marathon",
        "athletics",
    ]),
    ("Film", ["screening", "film festival", "movie night"]),
    ("Community & Civic", [
        "town hall", "board of education", "council meeting", "library",
        "civic", "volunteer", "blood drive", "fundraiser",
    ]),
    ("Theater & Performing Arts", [
        "musical", "theatre", "theater", "play", "recital", "ballet",
        "orchestra", "symphony", "choir", "opera",
    ]),
    ("Concert", [
        "concert", "tour", "live music", "band", "singer", "songwriter",
    ]),
]

# Ticketmaster/SeatGeek "segment" values map straight across when nothing
# more specific matches.
_SEGMENT_TYPE_MAP = {
    "music": "Concert",
    "arts & theatre": "Theater & Performing Arts",
    "sports": "Sports",
    "film": "Film",
    "miscellaneous": "Other",
}


def classify_type(title=None, venue=None, raw_category=None, source_segment=None):
    haystack = " ".join(filter(None, [title, venue, raw_category])).lower()
    for event_type, keywords in _TYPE_KEYWORDS:
        if any(kw in haystack for kw in keywords):
            return event_type
    if source_segment:
        mapped = _SEGMENT_TYPE_MAP.get(source_segment.strip().lower())
        if mapped:
            return mapped
    return "Other"


def classify_scale(
    is_school_or_community_source=False,
    seatgeek_score=None,
    price_max=None,
    major_price_threshold=150,
    midsize_price_threshold=50,
):
    """
    Scale tiers:
      Local-Community — anything from a school/library/town feed or a
                         manual entry by definition (these sources only
                         exist because the event isn't on a major
                         ticketing platform), OR a ticketed event with no
                         signal suggesting otherwise.
      Major            — SeatGeek popularity score above ~0.55, or a
                         Ticketmaster price ceiling above the configured
                         threshold (touring headliners charge more).
      Mid-size          — everything ticketed that doesn't clear the
                         Major bar.
    """
    if is_school_or_community_source:
        return "Local-Community"

    if seatgeek_score is not None:
        if seatgeek_score >= 0.55:
            return "Major"
        if seatgeek_score >= 0.2:
            return "Mid-size"
        return "Local-Community"

    if price_max is not None:
        if price_max >= major_price_threshold:
            return "Major"
        if price_max >= midsize_price_threshold:
            return "Mid-size"
        return "Local-Community"

    # No signal available — default to Mid-size rather than overclaiming
    # "Major" for an event we can't actually gauge.
    return "Mid-size"


def extract_max_price(price_display):
    """Pulls the highest number out of a price string like '$35-$150' or
    '$40+', for use with classify_scale(price_max=...)."""
    if not price_display:
        return None
    numbers = re.findall(r"[\d.]+", price_display.replace(",", ""))
    if not numbers:
        return None
    try:
        return max(float(n) for n in numbers)
    except ValueError:
        return None
