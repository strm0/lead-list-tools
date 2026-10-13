"""Scrape an event programme: every event page, one row per named person.

The programme is a paginated list of links. Each event page names its
participants and up to two contact persons. A person named in free text is
not picked up; only the structured parts of the page are read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

import pandas as pd

from .config import Settings
from .fetch import Fetcher
from .normalize import display_name
from .workbook import clean_text, write_workbook

PARTICIPANT, CONTACT, BOTH = "Participant", "Contact", "Both"
PARTICIPANT_COLUMNS = ["Name", "Title", "Email", "Organisation", "Source", "Event_IDs"]
# More than this is not a programme. A site that keeps inventing pages must not keep the scraper going.
MAX_INDEX_PAGES = 500
MAX_EVENT_PAGES = 20_000


class _IndexParser(HTMLParser):
    """Collects the event links on a list page and the link to the next page."""

    def __init__(self) -> None:
        super().__init__()
        self.event_links: list[str] = []
        self.next_page = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        if attributes.get("class") == "event-link":
            self.event_links.append(attributes.get("href", ""))
        elif attributes.get("rel") == "next":
            self.next_page = attributes.get("href", "")


@dataclass
class _Event:
    id: str = ""
    title: str = ""
    when: str = ""
    organiser: str = ""
    people: list[dict] = field(default_factory=list)


class _EventParser(HTMLParser):
    """Reads one event page into an _Event."""

    FIELDS = {"name": "Name", "title": "Title", "org": "Organisation", "email": "Email"}

    def __init__(self) -> None:
        super().__init__()
        self.event = _Event()
        self._section = ""          # "participants" or "contacts"
        self._person: dict | None = None
        self._person_tag = ""
        self._capture = ""          # which field the current text belongs to

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attributes = dict(attrs)
        css = attributes.get("class", "")
        if tag == "article":
            self.event.id = attributes.get("data-event-id", "")
        elif tag == "section":
            self._section = css
        elif tag == "time":
            self.event.when = attributes.get("datetime", "")
        elif tag == "h1":
            self._capture = "event-title"
        elif tag == "p" and css == "organiser":
            self._capture = "organiser"
        elif css == "person" and self._section:
            self._person = {"Name": "", "Title": "", "Organisation": "", "Email": "",
                            "Source": PARTICIPANT if self._section == "participants" else CONTACT}
            self._person_tag = tag
        elif self._person is not None and css in self.FIELDS:
            self._capture = css

    def handle_data(self, data: str) -> None:
        if self._capture == "event-title":
            self.event.title += data
        elif self._capture == "organiser":
            self.event.organiser += data
        elif self._capture and self._person is not None:
            self._person[self.FIELDS[self._capture]] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "span", "a") or (tag == "p" and self._capture == "organiser"):
            self._capture = ""
        if tag == "section":
            self._section = ""
        if self._person is not None and tag == self._person_tag:
            # Stray spaces inside a name or organisation are a typing slip, not a different spelling.
            # Control characters are no part of a name at all: they are dropped before the spaces are tidied.
            person = {key: " ".join(clean_text(value).split()) for key, value in self._person.items()}
            if person["Name"]:
                self.event.people.append(person)
            self._person = None


def parse_event(page: str) -> _Event:
    parser = _EventParser()
    parser.feed(page)
    parser.close()
    event = parser.event
    event.id, event.when = clean_text(event.id).strip(), clean_text(event.when).strip()
    event.title, event.organiser = clean_text(event.title), clean_text(event.organiser)
    return event


def _on_site(fetcher: Fetcher, page_url: str, link: str) -> str:
    """The full address a link points to, or "" if it leaves the site being scraped."""
    # Without the fragment: "#a" and "#b" on one address are one page.
    try:
        target = urldefrag(urljoin(page_url, link.strip())).url
    except ValueError:              # not an address at all, such as a broken IPv6 host
        return ""
    return target if fetcher.same_origin(target) else ""


def scrape(base_url: str, settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (one row per person per event, one row per event)."""
    fetcher = Fetcher(base_url, settings.cache / "programme", settings.delay_seconds,
                      settings.timeout_seconds, settings.retries)
    page_url = urljoin(base_url + "/", "programme/index.html")
    event_urls: dict[str, None] = {}
    visited: set[str] = set()
    left_site = 0
    while page_url and page_url not in visited:   # a "next" link that loops back must not loop forever
        if len(visited) >= MAX_INDEX_PAGES:       # nor one that always leads to a new page
            raise SystemExit(f"The programme list went on past {MAX_INDEX_PAGES} pages, at {page_url[:200]}. "
                             "That is more than a programme has; nothing was scraped.")
        visited.add(page_url)
        index = _IndexParser()
        index.feed(fetcher.text(page_url, page_url.rsplit("/", 1)[-1]))
        index.close()
        # A link to another site, another scheme or another port is counted and left alone.
        links = [_on_site(fetcher, page_url, link) for link in index.event_links if link.strip()]
        event_urls.update(dict.fromkeys(link for link in links if link))
        left_site += links.count("")
        if len(event_urls) > MAX_EVENT_PAGES:
            raise SystemExit(f"The programme lists more than {MAX_EVENT_PAGES} events. "
                             "That is more than a programme has; nothing was scraped.")
        following = _on_site(fetcher, page_url, index.next_page) if index.next_page.strip() else ""
        left_site += 1 if index.next_page.strip() and not following else 0
        page_url = following
    if left_site:
        print(f"  {left_site} links led away from {base_url} and were not followed")

    occurrences, events = [], []
    for url in event_urls:
        event = parse_event(fetcher.text(url, url.rsplit("/", 1)[-1]))
        events.append({"Event_ID": event.id, "Title": event.title.strip(), "Day": event.when[:10],
                       "Time": event.when[11:16], "Organiser": event.organiser.strip()})
        for person in event.people:
            occurrences.append({**person, "Event_ID": event.id})
    print(f"  {len(events)} event pages read ({fetcher.requests_made} fetched, the rest from cache)")
    return pd.DataFrame(occurrences), pd.DataFrame(events)


def _event_order(event_id: str) -> tuple:
    return (0, int(event_id)) if event_id.isdigit() else (1, event_id)


def one_row_per_person(occurrences: pd.DataFrame) -> pd.DataFrame:
    """Collapse to one row per (name, organisation as written).

    The same person under two spellings of an organisation stays as two rows
    here. Reconciling spellings is the cross-reference's job, with a person
    looking on, not something to do quietly at scrape time.
    """
    rows = []
    keys = [occurrences["Name"].str.lower(), occurrences["Organisation"].str.lower()]
    for _, group in occurrences.groupby(keys, sort=False):
        sources = set(group["Source"])
        emails = [e for e in group["Email"] if e]
        rows.append({
            "Name": display_name(group["Name"]),
            "Title": max(group["Title"], key=len),          # the fullest title
            "Email": emails[0] if emails else "",
            "Organisation": group["Organisation"].iloc[0],
            "Source": BOTH if len(sources) > 1 else sources.pop(),
            "Event_IDs": ", ".join(sorted(set(group["Event_ID"]), key=_event_order)),
        })
    frame = pd.DataFrame(rows, columns=PARTICIPANT_COLUMNS)
    return frame.sort_values(["Organisation", "Name"], key=lambda s: s.str.lower()).reset_index(drop=True)


def run(base_url: str, settings: Settings) -> dict:
    occurrences, events = scrape(base_url, settings)
    people = one_row_per_person(occurrences)
    path = write_workbook(settings.output / "participants.xlsx", {
        "All_Participants": people,
        "Occurrences": occurrences[["Event_ID", "Name", "Organisation", "Source"]],
        "Events": events,
    })
    counts = {"events": len(events), "person mentions": len(occurrences), "people": len(people)}
    print(f"  {counts['person mentions']} mentions of a person, {counts['people']} rows after merging repeats")
    print(f"  wrote {path}")
    return counts
