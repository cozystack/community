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
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

PLAYLIST_ID = "PLEIgpkcPkMHaXqndo8iMMLS64p4sBkHPg"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?playlist_id={PLAYLIST_ID}"
ARCHIVE = Path(__file__).resolve().parent.parent / "meetings" / "README.md"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

EM_DASH = "—"
ROW = re.compile(r"^\| (?P<date>\d{4}-\d{2}-\d{2}) \| (?P<recording>.*?) \| (?P<topics>.*?) \|$")
VIDEO_ID = re.compile(r"youtube\.com/watch\?v=([\w-]+)")
COUNTS = re.compile(r"^Meetings listed: \d+\. Recordings available: \d+\.")

# Meetings are held on Thursdays and the recording is published the same day or
# shortly after, so the publication date snaps back to the meeting date. This beats
# parsing the video title, where the day and month have been swapped often enough to
# make an unambiguous title unreliable.
THURSDAY = 3
MAX_SNAP_DAYS = 4


def fetch_entries() -> list[tuple[str, str, dt.date]]:
    with urllib.request.urlopen(FEED_URL, timeout=30) as response:
        feed = ET.fromstring(response.read())
    entries = []
    for entry in feed.findall("atom:entry", NS):
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        title = (entry.findtext("atom:title", namespaces=NS) or "").strip()
        published = entry.findtext("atom:published", namespaces=NS)
        if not video_id or not published:
            continue
        entries.append((video_id, title, dt.datetime.fromisoformat(published).date()))
    return sorted(entries, key=lambda entry: entry[2])


def meeting_date(published: dt.date) -> tuple[dt.date, bool]:
    """Return the meeting date for a publication date, and whether it looks off."""
    offset = (published.weekday() - THURSDAY) % 7
    candidates = [published - dt.timedelta(days=offset), published - dt.timedelta(days=offset - 7)]
    nearest = min(candidates, key=lambda day: abs((published - day).days))
    return nearest, abs((published - nearest).days) > MAX_SNAP_DAYS


def main() -> int:
    lines = ARCHIVE.read_text(encoding="utf-8").splitlines()
    rows = [index for index, line in enumerate(lines) if ROW.match(line)]
    if not rows:
        print("no meeting rows found in the archive", file=sys.stderr)
        return 1

    top = rows[0]
    newest_listed = ROW.match(lines[top])["date"]
    known_videos = {
        video for index in rows for video in VIDEO_ID.findall(ROW.match(lines[index])["recording"])
    }

    added: list[str] = []
    flagged: list[str] = []
    for video_id, title, published in fetch_entries():
        if video_id in known_videos:
            continue
        date, uncertain = meeting_date(published)
        if str(date) <= newest_listed:
            continue
        link = f"[Watch](https://www.youtube.com/watch?v={video_id})"
        lines.insert(top, f"| {date} | {link} |  |")
        added.append(f"{date} — {title} (published {published})")
        if uncertain:
            flagged.append(f"{date} — publication was not near a Thursday: {title}")

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
