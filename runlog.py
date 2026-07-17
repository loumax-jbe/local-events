"""
runlog.py
---------
Lightweight collector sources use to record what they actually checked
on each run — not just "ticketmaster: 40 events" but per-feed detail
("McCarter Theatre: 12 events", "State Theatre NJ: FAILED — selector
returned 0 cards"). scraper.py persists this after every run so you have
a standing answer to "what's actually being checked, and what's broken
or missing" without reading console output.

Usage from a source module:
    import runlog
    runlog.record("McCarter Theatre", status="ok", count=12)
    runlog.record("State Theatre NJ", status="error", error="selector matched 0 cards")
"""

_entries = []


def reset():
    """Call once at the start of a scraper.py run."""
    _entries.clear()


def record(name, status, count=0, error=None, detail=None):
    """
    status: "ok" (found events), "empty" (ran fine, zero events — worth a
            look but not necessarily broken), "error" (failed to run),
            or "not_configured" (enabled but missing a key/URL/etc).
    """
    _entries.append({
        "name": name,
        "status": status,
        "count": count,
        "error": error,
        "detail": detail,
    })


def get_entries():
    return list(_entries)
