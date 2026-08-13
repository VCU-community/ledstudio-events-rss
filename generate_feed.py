#!/usr/bin/env python3
"""Generate a date-gated RSS feed for LEDstudio events."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


SHEET_ID = "1f3V8F2QVgWE5mCzzt8YRRDsMTDMBe-i09qiAHjbVR94"
SHEET_TAB = "Main"
SOURCE_PAGE_URL = "https://ledstudio.vcu.edu/news-and-events/events/"
PAGES_BASE_URL = "https://vcu-community.github.io/ledstudio-events-rss/"
FEED_URL = urllib.parse.urljoin(PAGES_BASE_URL, "rss.xml")
SOURCE_TIMEZONE = ZoneInfo("America/New_York")
RELEASE_DAYS_BEFORE_EVENT = 14
USER_AGENT = (
    "VCU-Community-LEDstudio-Events-RSS/1.0 "
    "(+https://github.com/VCU-Community/ledstudio-events-rss)"
)

DATE_COLUMN = "CHOOSE THE DATE"
START_COLUMN = "START TIME (HH:MM AM/PM)"
END_COLUMN = "END TIME (HH:MM AM/PM)"
TITLE_COLUMN = "TITLE"
DESCRIPTION_COLUMN = "DESCRIPTION"
LOCATION_COLUMN = "LOCATION"
REGISTRATION_COLUMN = "REGISTRATION LINK"
ANCHOR_COLUMN = "EVENT ID ANCHOR"
UID_COLUMN = "EVENT UID"

REQUIRED_COLUMNS = {
    DATE_COLUMN,
    START_COLUMN,
    END_COLUMN,
    TITLE_COLUMN,
    DESCRIPTION_COLUMN,
    LOCATION_COLUMN,
    REGISTRATION_COLUMN,
    ANCHOR_COLUMN,
}

ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("atom", ATOM_NS)


class FeedError(RuntimeError):
    """Raised when source data is unsafe to publish."""


@dataclass(frozen=True)
class Resource:
    name: str
    url: str


@dataclass(frozen=True)
class Event:
    event_date: date
    start_time: time
    end_time: time | None
    title: str
    description: str
    location: str
    registration_url: str
    anchor: str
    uid: str
    resources: tuple[Resource, ...]

    @property
    def release_date(self) -> date:
        return self.event_date - timedelta(days=RELEASE_DAYS_BEFORE_EVENT)

    @property
    def event_url(self) -> str:
        fragment = urllib.parse.quote(self.anchor, safe="-._~")
        return SOURCE_PAGE_URL + "#" + fragment

    @property
    def guid(self) -> str:
        return "urn:vcu:ledstudio:event:" + self.uid


@dataclass(frozen=True)
class Selection:
    published: tuple[Event, ...]
    delayed: tuple[Event, ...]
    future: tuple[Event, ...]
    past: tuple[Event, ...]


def normalize_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def normalize_text(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def sheet_url() -> str:
    query = urllib.parse.urlencode({"tqx": "out:json", "sheet": SHEET_TAB})
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq?{query}"


def fetch_text(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise FeedError(f"Source returned HTTP {response.status}")
            return response.read().decode("utf-8")
    except FeedError:
        raise
    except Exception as exc:
        raise FeedError(f"Could not fetch event data: {exc}") from exc


def unwrap_gviz(text: str) -> Mapping[str, object]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise FeedError("The Google Sheet response did not contain JSON")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise FeedError(f"The Google Sheet response was invalid JSON: {exc}") from exc
    if payload.get("status") == "error":
        raise FeedError("Google Sheets reported an error response")
    if not isinstance(payload.get("table"), dict):
        raise FeedError("The Google Sheet response did not contain a table")
    return payload


def rows_from_gviz(text: str) -> list[dict[str, str]]:
    payload = unwrap_gviz(text)
    table = payload["table"]
    assert isinstance(table, dict)
    columns = [normalize_label(col.get("label")) for col in table.get("cols", [])]
    available = {column for column in columns if column}
    missing = sorted(REQUIRED_COLUMNS - available)
    if missing:
        raise FeedError("Required sheet columns are missing: " + ", ".join(missing))

    rows: list[dict[str, str]] = []
    for raw_row in table.get("rows", []):
        cells = raw_row.get("c", []) if isinstance(raw_row, dict) else []
        row: dict[str, str] = {}
        for index, label in enumerate(columns):
            if not label:
                continue
            cell = cells[index] if index < len(cells) and cells[index] else {}
            value = cell.get("f") if cell.get("f") is not None else cell.get("v", "")
            row[label] = normalize_text(value)
        if any(row.values()):
            rows.append(row)
    if not rows:
        raise FeedError("The source sheet contained no event rows")
    return rows


def parse_date(value: str) -> date:
    formats = ("%A, %B %d, %Y", "%B %d, %Y")
    for pattern in formats:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise FeedError(f"Unrecognized event date: {value!r}")


def parse_time(value: str, *, required: bool) -> time | None:
    if not value:
        if required:
            raise FeedError("An event is missing its start time")
        return None
    cleaned = re.sub(r"\s+", " ", value.upper()).strip()
    for pattern in ("%I:%M %p", "%I %p"):
        try:
            return datetime.strptime(cleaned, pattern).time()
        except ValueError:
            pass
    raise FeedError(f"Unrecognized event time: {value!r}")


def absolute_web_url(value: str, *, field: str) -> str:
    url = urllib.parse.urljoin(SOURCE_PAGE_URL, value)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise FeedError(f"{field} is not a valid web URL: {value!r}")
    return url


def resources_from_row(row: Mapping[str, str]) -> tuple[Resource, ...]:
    resources: list[Resource] = []
    for number in range(1, 21):
        name = normalize_text(row.get(f"RESOURCE {number} NAME", ""))
        url = normalize_text(row.get(f"RESOURCE {number} LINK", ""))
        if not name and not url:
            continue
        if not name or not url:
            raise FeedError(f"Resource {number} must have both a name and a link")
        resources.append(Resource(name, absolute_web_url(url, field=f"Resource {number} link")))
    return tuple(resources)


def event_from_row(row: Mapping[str, str], row_number: int) -> Event:
    def required(column: str) -> str:
        value = normalize_text(row.get(column, ""))
        if not value:
            raise FeedError(f"Row {row_number} is missing {column}")
        return value

    try:
        anchor = required(ANCHOR_COLUMN)
        uid = normalize_text(row.get(UID_COLUMN, "")) or anchor
        if not re.fullmatch(r"[A-Za-z0-9._~-]+", uid):
            raise FeedError(
                f"Row {row_number} has an unsafe EVENT UID/EVENT ID ANCHOR: {uid!r}"
            )
        return Event(
            event_date=parse_date(required(DATE_COLUMN)),
            start_time=parse_time(required(START_COLUMN), required=True),  # type: ignore[arg-type]
            end_time=parse_time(normalize_text(row.get(END_COLUMN, "")), required=False),
            title=required(TITLE_COLUMN),
            description=required(DESCRIPTION_COLUMN),
            location=required(LOCATION_COLUMN),
            registration_url=(
                absolute_web_url(row[REGISTRATION_COLUMN], field="Registration link")
                if normalize_text(row.get(REGISTRATION_COLUMN, ""))
                else ""
            ),
            anchor=anchor,
            uid=uid,
            resources=resources_from_row(row),
        )
    except FeedError as exc:
        if str(exc).startswith(f"Row {row_number}"):
            raise
        raise FeedError(f"Row {row_number}: {exc}") from exc


def parse_events(rows: Iterable[Mapping[str, str]]) -> list[Event]:
    events = [event_from_row(row, index) for index, row in enumerate(rows, start=2)]
    by_guid: dict[str, Event] = {}
    for event in events:
        previous = by_guid.get(event.guid)
        if previous is not None and previous != event:
            raise FeedError(f"Conflicting events use the same GUID: {event.guid}")
        by_guid[event.guid] = event
    return sorted(by_guid.values(), key=lambda event: (event.event_date, event.start_time, event.title))


def select_events(events: Sequence[Event], today: date) -> Selection:
    published: list[Event] = []
    delayed: list[Event] = []
    future: list[Event] = []
    past: list[Event] = []
    for event in events:
        if event.event_date < today:
            past.append(event)
        elif event.release_date > today:
            future.append(event)
        elif not event.registration_url:
            delayed.append(event)
        else:
            published.append(event)
    return Selection(tuple(published), tuple(delayed), tuple(future), tuple(past))


def display_date(value: date) -> str:
    return f"{value.strftime('%A, %B')} {value.day}, {value.year}"


def display_time(value: time) -> str:
    hour = value.strftime("%I").lstrip("0") or "0"
    minute = value.strftime("%M")
    meridiem = value.strftime("%p").lower().replace("am", "a.m.").replace("pm", "p.m.")
    return f"{hour}:{minute} {meridiem}"


def event_description_text(event: Event) -> str:
    description = re.sub(r"\s+", " ", event.description).strip()
    time_text = display_time(event.start_time)
    if event.end_time is not None:
        time_text += "–" + display_time(event.end_time)

    parts = [
        description,
        f"Date: {display_date(event.event_date)}",
        f"Time: {time_text}",
        f"Location: {event.location}",
        f"Register: {event.registration_url}",
    ]
    if event.resources:
        resources = "; ".join(
            f"{resource.name} — {resource.url}" for resource in event.resources
        )
        parts.append(f"Resources: {resources}")
    parts.append(f"Event details: {event.event_url}")
    return " | ".join(parts)


def add_text(parent: ET.Element, name: str, value: str, attributes: dict[str, str] | None = None) -> ET.Element:
    child = ET.SubElement(parent, name, attributes or {})
    child.text = value
    return child


def build_rss(events: Sequence[Event], built_at: datetime) -> bytes:
    if built_at.tzinfo is None:
        raise FeedError("Build time must be timezone-aware")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    add_text(channel, "title", "LEDstudio Upcoming Events")
    add_text(channel, "link", SOURCE_PAGE_URL)
    add_text(
        channel,
        "description",
        "LEDstudio events released for commUNity two weeks before their event dates.",
    )
    add_text(channel, "language", "en-us")
    add_text(channel, "lastBuildDate", format_datetime(built_at.astimezone(timezone.utc), usegmt=True))
    add_text(channel, "generator", "ledstudio-events-rss")
    add_text(channel, "ttl", "360")
    ET.SubElement(
        channel,
        f"{{{ATOM_NS}}}link",
        {"href": FEED_URL, "rel": "self", "type": "application/rss+xml"},
    )

    for event in events:
        item = ET.SubElement(channel, "item")
        add_text(item, "title", event.title)
        add_text(item, "link", event.event_url)
        add_text(item, "guid", event.guid, {"isPermaLink": "false"})
        release_at = datetime.combine(event.release_date, time(9, 0), SOURCE_TIMEZONE)
        add_text(item, "pubDate", format_datetime(release_at.astimezone(timezone.utc), usegmt=True))
        add_text(item, "description", event_description_text(event))

    ET.indent(rss, space="  ")
    document = ET.tostring(rss, encoding="utf-8", xml_declaration=True)
    try:
        ET.fromstring(document)
    except ET.ParseError as exc:
        raise FeedError(f"Generated RSS was not valid XML: {exc}") from exc
    return document


def build_status_page(selection: Selection, built_at: datetime, source_count: int) -> str:
    def event_list(events: Sequence[Event]) -> str:
        if not events:
            return "<p>None.</p>"
        return "<ul>" + "".join(
            f'<li><a href="{html.escape(event.event_url, quote=True)}">'
            f"{html.escape(event.title)}</a> — {html.escape(display_date(event.event_date))}</li>"
            for event in events
        ) + "</ul>"

    delayed_section = ""
    if selection.delayed:
        delayed_section = (
            "<h2>Delayed: registration needed</h2>"
            "<p>These eligible events will enter the feed after a registration link is added.</p>"
            + event_list(selection.delayed)
        )

    if selection.published:
        previews = "".join(
            '<article class="post-preview">'
            f'<h2><a href="{html.escape(event.event_url, quote=True)}">'
            f"{html.escape(event.title)}</a></h2>"
            f'<p class="post-body">{html.escape(event_description_text(event))}</p>'
            "</article>"
            for event in selection.published
        )
    else:
        previews = '<p class="empty">No events are currently inside the publication window.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LEDstudio Events RSS</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ font: 1rem/1.55 system-ui, sans-serif; max-width: 52rem; margin: 3rem auto; padding: 0 1rem; color: #222; background: #f5f6f8; }}
    h1, h2 {{ line-height: 1.2; }}
    a {{ color: #2657a7; }}
    .intro, .technical {{ background: #fff; border: 1px solid #dfe2e7; border-radius: .65rem; padding: 1.25rem; }}
    .technical {{ margin-top: 2rem; }}
    .post-preview {{ background: #fff; border: 1px solid #dfe2e7; border-radius: .65rem; padding: 1.4rem; margin: 1.25rem 0; box-shadow: 0 .15rem .55rem rgb(0 0 0 / 7%); }}
    .post-preview h2 {{ font-size: 1.25rem; margin: 0 0 1rem; }}
    .post-preview h2 a {{ color: #222; text-decoration: none; }}
    .post-body {{ margin: 0; }}
    .summary {{ background: #f4f4f4; padding: 1rem; border-left: .35rem solid #f8b300; }}
    .empty {{ font-style: italic; }}
  </style>
</head>
<body>
  <section class="intro">
    <h1>LEDstudio Events RSS Preview</h1>
    <p>The content below mirrors what each current RSS item sends to commUNity. Final typography and link styling are controlled by commUNity.</p>
    <p><a href="rss.xml">Open rss.xml</a> · <a href="{html.escape(SOURCE_PAGE_URL, quote=True)}">View source events</a></p>
  </section>
  <main>
    {previews}
  </main>
  <section class="technical">
    <h2>Feed status</h2>
    <div class="summary">
      <strong>Last successful build:</strong> {html.escape(built_at.astimezone(SOURCE_TIMEZONE).isoformat(timespec='seconds'))}<br>
      <strong>Source rows:</strong> {source_count}<br>
      <strong>Items in feed:</strong> {len(selection.published)}
    </div>
    {delayed_section}
  </section>
</body>
</html>
"""


def write_outputs(output_dir: Path, rss: bytes, status_page: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir.parent) as temp_name:
        temp_dir = Path(temp_name)
        rss_path = temp_dir / "rss.xml"
        index_path = temp_dir / "index.html"
        rss_path.write_bytes(rss)
        index_path.write_text(status_page, encoding="utf-8")
        rss_path.replace(output_dir / "rss.xml")
        index_path.replace(output_dir / "index.html")


def generate(*, output_dir: Path, today: date | None = None, source_text: str | None = None) -> Selection:
    built_at = datetime.now(timezone.utc)
    local_today = today or built_at.astimezone(SOURCE_TIMEZONE).date()
    text = source_text if source_text is not None else fetch_text(sheet_url())
    rows = rows_from_gviz(text)
    events = parse_events(rows)
    selection = select_events(events, local_today)
    rss = build_rss(selection.published, built_at)
    status = build_status_page(selection, built_at, len(rows))
    write_outputs(output_dir, rss, status)
    return selection


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("public"))
    parser.add_argument("--today", type=date.fromisoformat, help="Override local date for testing")
    parser.add_argument("--source-file", type=Path, help="Read a saved GViz response instead of the live sheet")
    args = parser.parse_args(argv)
    try:
        source_text = args.source_file.read_text(encoding="utf-8") if args.source_file else None
        selection = generate(output_dir=args.output, today=args.today, source_text=source_text)
    except (FeedError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Published {len(selection.published)} event(s).")
    if selection.delayed:
        print(
            "WARNING: Delayed event(s) missing registration links: "
            + "; ".join(event.title for event in selection.delayed),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
