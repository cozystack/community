#!/usr/bin/env python3
"""Add newly published community meeting recordings to the top of meetings/README.md.

Reads the public Atom feed of the community meetings YouTube playlist, which needs
no API key and carries the fifteen most recent entries — plenty for a job that runs
weekly against a fortnightly meeting.

Scope is deliberately narrow: only meetings newer than the newest row already in the
table. History is never touched, so a wrong date or a missing link in an old row
stays a job for a human, not something this script tries to guess at.

Topics are left empty for a human to fill in while reviewing the pull request. The
script has no way to know what was discussed, and a plausible guess would be worse
than a blank.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

PLAYLIST_ID = "PLEIgpkcPkMHaXqndo8iMMLS64p4sBkHPg"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}"
ARCHIVE = Path(__file__).resolve().parent.parent / "meetings" / "README.md"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

EM_DASH = "—"
ROW = re.compile(r"^\| (?P<date>\d{4}-\d{2}-\d{2}) \| (?P<recording>.*?) \| (?P<topics>.*?) \|$")
VIDEO_ID = re.compile(r"youtube\.com/watch\?v=([\w-]+)")
COUNTS = re.compile(r"^Meetings listed: \d+\. Recordings available: \d+\.")

# Meetings are held on Thursdays, fortnightly, and the recording is published
# anywhere from the same day to more than a week later. The publication date alone
# therefore cannot say which Thursday a recording belongs to: snapping to the
# nearest Thursday puts a recording published on the Tuesday or Friday after the
# meeting on the following week's Thursday, a date on which no meeting was held.
#
# The video description and title carry the meeting date ("Cozystack community
# meeting 2026-17-09"), but the day and month have been swapped often enough to
# make either order possible. Both readings are tried, and a reading is accepted
# only if it is a Thursday shortly before the publication date — which, for
# fortnightly meetings, leaves exactly one of them. When the text has no usable
# date, the recording is placed on the latest fortnightly slot after the newest
# listed meeting, never after the publication date, and flagged for review.
THURSDAY = 3
CADENCE = dt.timedelta(days=14)
MAX_PUBLISH_LAG = dt.timedelta(days=14)
TEXT_DATE = re.compile(r"meeting\s+(\d{4})-(\d{2})-(\d{2})", re.IGNORECASE)
FETCH_ATTEMPTS = 4
FETCH_BACKOFF_SECONDS = 30


def fetch_feed() -> bytes:
    """Fetch the playlist feed, retrying through YouTube's transient 404 spells."""
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(FEED_URL, timeout=30) as response:
                return response.read()
        except urllib.error.URLError as error:
            if attempt == FETCH_ATTEMPTS:
                raise
            print(f"feed fetch failed ({error}), retrying", file=sys.stderr)
            time.sleep(FETCH_BACKOFF_SECONDS * attempt)
    raise AssertionError("unreachable")


def fetch_entries() -> list[tuple[str, str, str, dt.date]]:
    feed = ET.fromstring(fetch_feed())
    entries = []
    for entry in feed.findall("atom:entry", NS):
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        title = (entry.findtext("atom:title", namespaces=NS) or "").strip()
        description = entry.findtext("media:group/media:description", default="", namespaces=NS)
        published = entry.findtext("atom:published", namespaces=NS)
        if not video_id or not published:
            continue
        entries.append((video_id, title, description, dt.datetime.fromisoformat(published).date()))
    return sorted(entries, key=lambda entry: entry[3])


def dates_in_text(text: str) -> set[dt.date]:
    found = set()
    for year, first, second in TEXT_DATE.findall(text):
        for month, day in ((first, second), (second, first)):
            try:
                found.add(dt.date(int(year), int(month), int(day)))
            except ValueError:
                pass
    return found


def meeting_date(text: str, published: dt.date, newest_listed: dt.date) -> tuple[dt.date, bool]:
    """Return the meeting date for a recording, and whether it was guessed."""
    plausible = {
        day
        for day in dates_in_text(text)
        if day.weekday() == THURSDAY and dt.timedelta(0) <= published - day <= MAX_PUBLISH_LAG
    }
    if len(plausible) == 1:
        return plausible.pop(), False
    slots = (published - newest_listed) // CADENCE
    return newest_listed + slots * CADENCE, True


def main() -> int:
    lines = ARCHIVE.read_text(encoding="utf-8").splitlines()
    rows = [index for index, line in enumerate(lines) if ROW.match(line)]
    if not rows:
        print("no meeting rows found in the archive", file=sys.stderr)
        return 1

    top = rows[0]
    newest_listed = dt.date.fromisoformat(ROW.match(lines[top])["date"])
    known_videos = {
        video for index in rows for video in VIDEO_ID.findall(ROW.match(lines[index])["recording"])
    }

    added: list[str] = []
    flagged: list[str] = []
    for video_id, title, description, published in fetch_entries():
        if video_id in known_videos:
            continue
        date, uncertain = meeting_date(f"{title}\n{description}", published, newest_listed)
        if date <= newest_listed:
            continue
        link = f"[Watch](https://www.youtube.com/watch?v={video_id})"
        lines.insert(top, f"| {date} | {link} |  |")
        added.append(f"{date} — {title} (published {published})")
        if uncertain:
            flagged.append(f"{date} — no meeting date in the title or description: {title}")

    if not added:
        print("no new recordings")
        return 0

    rows = [index for index, line in enumerate(lines) if ROW.match(line)]
    recordings = sum(1 for index in rows if ROW.match(lines[index])["recording"].strip() != EM_DASH)
    for index, line in enumerate(lines):
        if COUNTS.match(line):
            lines[index] = COUNTS.sub(
                f"Meetings listed: {len(rows)}. Recordings available: {recordings}.", line
            )

    ARCHIVE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("New meetings added — fill in the Topics column before merging:")
    print("\n".join(f"- {note}" for note in added))
    if flagged:
        print("Worth a look, the date was derived rather than read:")
        print("\n".join(f"- {note}" for note in flagged))
    return 0


if __name__ == "__main__":
    sys.exit(main())
