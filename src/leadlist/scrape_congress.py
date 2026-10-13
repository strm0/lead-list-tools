"""Scrape a congress platform through its JSON API: speakers and sponsors.

The session list is fetched day by day and page by page, then each session
is fetched for its chairs and talks. Whether a speaker works for a sponsor is
decided with the shared normaliser and alias table, by exact match. A speaker
whose affiliation is spelled in a way the table does not know is listed as
not matched, and a person can add the spelling.
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import quote

import pandas as pd

from .config import Settings
from .crossref import load_meetings, load_won
from .fetch import Fetcher
from .normalize import AliasTable, tidy
from .workbook import write_workbook

# A programme often holds a slot before the speaker is known. Such an entry is made only of words like these.
_UNDECIDED = {"tba", "tbc", "tbd"}
_NOBODY_IN_PARTICULAR = {"all", "the", "faculty", "panel", "speaker", "speakers"}
PER_PAGE = 10
# More than this is not a congress. The API says how many pages and days there are, and it must not be believed blindly.
MAX_PAGES_PER_DAY = 200
MAX_DAYS = 60
MAX_SESSIONS = 20_000
MAX_ID_LENGTH = 100


def is_placeholder(name: str) -> bool:
    """True for "TBA", "Faculty", "All speakers" and the like: a slot, not a person."""
    words = name.lower().split()
    return not words or all(word in _UNDECIDED or word in _NOBODY_IN_PARTICULAR for word in words)


def _person(record: object) -> dict | None:
    if not isinstance(record, dict):
        return None
    name = f"{tidy(record.get('first_name'))} {tidy(record.get('last_name'))}".strip()
    if is_placeholder(name):
        return None
    return {"Person": name, "Country": tidy(record.get("country")), "Affiliation": tidy(record.get("affiliation"))}


def _total_pages(headers: dict, url: str) -> int:
    """The page count the API reports. No header means one page."""
    stated = str(headers.get("x-total-pages", "1")).strip()
    # Digits only: no sign, no decimal point, nothing int() would read from another script.
    if not (stated.isascii() and stated.isdigit()):
        raise SystemExit(f"{url} reports its page count as {stated[:40]!r}, which is not a whole number. "
                         "Nothing was scraped.")
    if len(stated) > 9 or int(stated) > MAX_PAGES_PER_DAY:
        raise SystemExit(f"{url} reports {stated[:40]} pages for one day. More than {MAX_PAGES_PER_DAY} "
                         "is not believed; nothing was scraped.")
    return int(stated)      # 0 is what some platforms say for a day with no sessions: the loop ends


def _id_in_url(session_id: object) -> str:
    """A session id as one path segment. The id comes from the API, so it is not trusted as part of an address."""
    if isinstance(session_id, bool) or not isinstance(session_id, (int, str)):
        raise SystemExit(f"The congress API gave a session id that is neither a number nor text: "
                         f"{str(session_id)[:80]!r}. Nothing was scraped.")
    text = str(session_id)
    if not text or len(text) > MAX_ID_LENGTH or text in (".", ".."):
        raise SystemExit(f"The congress API gave a session id that cannot be used: {text[:80]!r}. "
                         "Nothing was scraped.")
    return quote(text, safe="")     # a slash in the id must not become a slash in the address


def _listed(body: object, of: type, url: str) -> list:
    if not isinstance(body, list) or not all(isinstance(item, of) for item in body):
        raise SystemExit(f"{url} did not answer with the list that was expected. Nothing was scraped.")
    return body


def collect(base_url: str, fetcher: Fetcher) -> tuple[list[dict], list[dict]]:
    """Returns (sponsors, sessions with their detail)."""
    api = f"{base_url}/api/v1"
    days = _listed(fetcher.json(f"{api}/days", None, "days.json")[0], str, f"{api}/days")
    sponsors = _listed(fetcher.json(f"{api}/sponsors", None, "sponsors.json")[0], dict, f"{api}/sponsors")
    if len(days) > MAX_DAYS:
        raise SystemExit(f"{api}/days lists {len(days)} days. More than {MAX_DAYS} is not believed; nothing was scraped.")
    summaries: dict[int | str, dict] = {}
    for day in days:
        page, total_pages = 1, 1
        while page <= total_pages:
            if page > MAX_PAGES_PER_DAY:
                raise SystemExit(f"{api}/sessions went on past {MAX_PAGES_PER_DAY} pages for {day[:40]}. "
                                 "Nothing was scraped.")
            body, headers = fetcher.json(
                f"{api}/sessions", {"date": day, "page": page, "per_page": PER_PAGE}, f"sessions_{day}_p{page}.json")
            total_pages = _total_pages(headers, f"{api}/sessions")
            for summary in _listed(body, dict, f"{api}/sessions"):
                session_id = summary.get("id")
                _id_in_url(session_id)                      # refused here, before anything is fetched with it
                summaries.setdefault(session_id, summary)
            if len(summaries) > MAX_SESSIONS:
                raise SystemExit(f"{api}/sessions lists more than {MAX_SESSIONS} sessions. Nothing was scraped.")
            page += 1
    sessions = []
    for session_id in summaries:
        quoted = _id_in_url(session_id)
        session, _ = fetcher.json(f"{api}/sessions/{quoted}", None, f"session_{quoted}.json")
        if not isinstance(session, dict):
            raise SystemExit(f"{api}/sessions/{quoted} did not answer with a session. Nothing was scraped.")
        sessions.append(session)
    return sponsors, sessions


def build_sheets(sponsors: list[dict], sessions: list[dict], aliases: AliasTable,
                 known_accounts: set[str], paid_accounts: set[str]) -> dict[str, pd.DataFrame]:
    sponsor_by_key = {aliases.key(s["name"]).key: s["name"] for s in sponsors}
    sponsor_names = {s["id"]: s["name"] for s in sponsors}
    sessions_sponsored: dict[str, int] = defaultdict(int)
    employed: dict[str, set] = defaultdict(set)
    speakers: dict[tuple, dict] = {}
    raw_rows = []

    for session in sessions:
        people = [(_person(chair), "Chair") for chair in session.get("chairs", [])]
        people += [(_person(talk.get("speaker", {})), "Speaker") for talk in session.get("talks", [])]
        named = [(person, role) for person, role in people if person]
        session_sponsors = [sponsor_names[i] for i in session.get("sponsor_ids", []) if i in sponsor_names]
        for name in session_sponsors:
            sessions_sponsored[name] += 1
        for person, role in named:
            entry = speakers.setdefault((person["Person"], person["Country"]), {
                **person, "roles": set(), "sessions": set(),
                "Employed_By_Sponsor": sponsor_by_key.get(aliases.key(person["Affiliation"]).key, ""),
            })
            entry["roles"].add(role)
            entry["sessions"].add(session["title"])
            if entry["Employed_By_Sponsor"]:
                employed[entry["Employed_By_Sponsor"]].add(person["Person"])
        raw_rows.append({
            "Session_ID": session["id"], "Title": session["title"], "Type": session["type"],
            "Date": session["starts_at"][:10], "Time": f"{session['starts_at'][11:16]} to {session['ends_at'][11:16]}",
            "Sponsors": "; ".join(session_sponsors), "People_Named": len(named),
            "Placeholders_Skipped": len(people) - len(named),
        })

    speaker_rows = [{
        "Person": s["Person"], "Country": s["Country"], "Affiliation": s["Affiliation"],
        "Employed_By_Sponsor": s["Employed_By_Sponsor"], "Roles": ", ".join(sorted(s["roles"])),
        "Sessions": len(s["sessions"]), "Session_Titles": "; ".join(sorted(s["sessions"])),
    } for s in speakers.values()]
    sponsor_rows = [{
        "Company": s["name"], "Tier": s["tier"],
        "Sessions_Sponsored": sessions_sponsored[s["name"]],
        "Speakers_Employed": len(employed[s["name"]]),
        "Known_Account": "Yes" if aliases.key(s["name"]).key in known_accounts else "No",
        "Paid_Account": "Yes" if aliases.key(s["name"]).key in paid_accounts else "No",
    } for s in sponsors]
    tier_order = {tier: rank for rank, tier in enumerate(dict.fromkeys(s["tier"] for s in sponsors))}
    sponsor_rows.sort(key=lambda r: (tier_order[r["Tier"]], -r["Sessions_Sponsored"], r["Company"]))
    return {
        "Speakers": pd.DataFrame(speaker_rows).sort_values(["Employed_By_Sponsor", "Person"], ascending=[False, True]),
        "Sponsors": pd.DataFrame(sponsor_rows),
        "Sessions_Raw": pd.DataFrame(raw_rows),
    }


def run(base_url: str, settings: Settings) -> dict:
    fetcher = Fetcher(base_url, settings.cache / "congress", settings.delay_seconds,
                      settings.timeout_seconds, settings.retries)
    sponsors, sessions = collect(base_url, fetcher)
    aliases = AliasTable.load(settings.aliases)
    known = paid = set()
    if settings.meetings_file.exists() and settings.won_file.exists():
        known = set(load_meetings(settings, aliases)["account_key"])
        paid = set(load_won(settings, aliases)["account_key"])
    try:
        sheets = build_sheets(sponsors, sessions, aliases, known, paid)
    except (KeyError, TypeError, AttributeError) as problem:
        # The API is someone else's. An answer in a shape this scraper does not know stops here, plainly.
        raise SystemExit(f"The congress API answered in a shape this scraper does not know ({problem!r}). "
                         "Nothing was written.") from None
    path = write_workbook(settings.output / "congress.xlsx", sheets)
    tiers = sheets["Sponsors"]["Tier"].nunique()
    counts = {"sessions": len(sessions), "speakers": len(sheets["Speakers"]),
              "sponsors": len(sponsors), "tiers": tiers}
    print(f"  {counts['sessions']} sessions, {counts['speakers']} speakers, "
          f"{counts['sponsors']} sponsors in {tiers} tiers ({fetcher.requests_made} requests, the rest from cache)")
    print(f"  wrote {path}")
    return counts
