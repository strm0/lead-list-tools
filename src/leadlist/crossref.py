"""Cross-reference the scraped people against the CRM exports, in two phases.

Phase one normalises both sides, lists every place where different spellings
land on the same key, writes that list to an audit workbook and stops. A
person reads it and edits the alias table. Phase two runs only when that
person starts it, and writes the lead lists.

Matching is exact on the normalised forms: there is no similarity score and
nothing is matched on resemblance. Names that normalise alike are matched,
and every place where that joins different spellings is listed for a person,
who can keep them apart. Spellings that do not normalise alike are joined
only by a line in the alias table.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from .config import Settings
from .normalize import AliasTable, display_name, person_key, split_left_tag
from .workbook import read_report, read_sheet, write_workbook

PERSONAL, ACCOUNT, PAID = "Personal_Match", "Account_Match_New_Person", "Paid_Account_Match"
TO_REVIEW, DECIDED = "to review", "decided in the alias table"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _keys(raw: pd.Series, aliases: AliasTable) -> tuple[pd.Series, pd.Series]:
    resolved = raw.map(aliases.key)
    return resolved.map(lambda r: r.key), resolved.map(lambda r: r.via)


def load_participants(settings: Settings, aliases: AliasTable) -> pd.DataFrame:
    source = settings.output / "participants.xlsx"
    if not source.exists():
        raise SystemExit("No participants yet. Run: leadlist scrape")
    people = read_sheet(source, "All_Participants")
    people["person_key"] = people["Name"].map(person_key)
    people["org_key"], people["via"] = _keys(people["Organisation"], aliases)
    return people


def load_meetings(settings: Settings, aliases: AliasTable) -> pd.DataFrame:
    columns = settings.meetings
    report = read_report(settings.meetings_file, [columns.account, columns.contact, columns.subject, columns.date])
    report = report[report[columns.account].notna()]
    contacts = report[columns.contact].map(lambda value: split_left_tag(value, columns.left_tag))
    meetings = pd.DataFrame({
        "account_raw": report[columns.account].astype(str).str.strip(),
        "contact": contacts.map(lambda pair: pair[0]),
        "left": contacts.map(lambda pair: pair[1]),
        "subject": report[columns.subject].fillna("").astype(str).str.strip(),
        "date": pd.to_datetime(report[columns.date], errors="coerce"),
    })
    meetings = meetings[meetings["account_raw"] != ""].reset_index(drop=True)
    meetings["contact_key"] = meetings["contact"].map(person_key)
    meetings["account_key"], meetings["via"] = _keys(meetings["account_raw"], aliases)
    return meetings


def load_won(settings: Settings, aliases: AliasTable) -> pd.DataFrame:
    columns = settings.won
    report = read_report(settings.won_file, [columns.account, columns.opportunity, columns.amount])
    # Group labels are printed on the first row of each group only.
    report[[columns.source, columns.account]] = report[[columns.source, columns.account]].ffill()
    report = report[report[columns.opportunity].notna() & report[columns.account].notna()]
    won = pd.DataFrame({
        "account_raw": report[columns.account].astype(str).str.strip(),
        "source": report[columns.source].fillna("").astype(str).str.strip(),
        "amount": pd.to_numeric(report[columns.amount], errors="coerce").fillna(0.0),
        "close": pd.to_datetime(report[columns.close_date], errors="coerce"),
    }).reset_index(drop=True)
    won["account_key"], won["via"] = _keys(won["account_raw"], aliases)
    return won


# ---------------------------------------------------------------------------
# Possible collisions
# ---------------------------------------------------------------------------

@dataclass
class Collision:
    """Different spellings that normalise to one key. Right or wrong? A person says."""
    key: str
    programme: list[str]
    meetings: list[str]
    won: list[str]
    people: int
    decided: bool
    # Kept-apart names that an undecided spelling here normalises like. A person says which it is.
    kept_apart: list[str] = field(default_factory=list)

    @property
    def spellings(self) -> list[str]:
        return _distinct(self.programme + self.meetings + self.won)


def _distinct(spellings) -> list[str]:
    """Spellings that differ by more than case and spacing, in a stable order."""
    seen: dict[str, str] = {}
    # Of two that differ by case only, show the one in ordinary mixed case.
    for spelling in sorted(set(spellings), key=lambda s: (s.isupper() or s.islower(), s)):
        seen.setdefault(" ".join(spelling.lower().split()), spelling)
    return sorted(seen.values())


def find_collisions(people: pd.DataFrame, meetings: pd.DataFrame, won: pd.DataFrame,
                    aliases: AliasTable) -> list[Collision]:
    by_key: dict[str, dict[str, list[str]]] = {}
    for label, frame, key_column, raw_column in (
        ("programme", people, "org_key", "Organisation"),
        ("meetings", meetings, "account_key", "account_raw"),
        ("won", won, "account_key", "account_raw"),
    ):
        for key, group in frame[frame[key_column] != ""].groupby(key_column):
            by_key.setdefault(key, {"programme": [], "meetings": [], "won": []})[label] = _distinct(group[raw_column])
    headcount = people.groupby("org_key")["person_key"].nunique()

    collisions = []
    for key, sides in by_key.items():
        everything = _distinct(sides["programme"] + sides["meetings"] + sides["won"])
        lookalikes = sorted({name for spelling in everything for name in aliases.kept_apart_lookalikes(spelling)})
        if len(everything) < 2 and not lookalikes:
            continue
        collisions.append(Collision(
            key=key, **sides, people=int(headcount.get(key, 0)),
            decided=not lookalikes and all(aliases.knows(spelling) for spelling in everything),
            kept_apart=lookalikes,
        ))
    return sorted(collisions, key=lambda c: (c.decided, -len(c.spellings), c.key))


def collisions_frame(collisions: list[Collision]) -> pd.DataFrame:
    return pd.DataFrame([{
        "Status": DECIDED if c.decided else TO_REVIEW,
        "Normalised": c.key,
        "Programme_Spellings": "; ".join(c.programme),
        "CRM_Meetings_Spellings": "; ".join(c.meetings),
        "CRM_Won_Spellings": "; ".join(c.won),
        "Distinct_Spellings": len(c.spellings),
        "People_At_Event": c.people,
        "Looks_Like_Kept_Apart": "; ".join(c.kept_apart),
    } for c in collisions], columns=["Status", "Normalised", "Programme_Spellings", "CRM_Meetings_Spellings",
                                     "CRM_Won_Spellings", "Distinct_Spellings", "People_At_Event",
                                     "Looks_Like_Kept_Apart"])


# ---------------------------------------------------------------------------
# Rollups and buckets
# ---------------------------------------------------------------------------

def _most_common(values) -> str:
    counted = Counter(v for v in values if v)
    return counted.most_common(1)[0][0] if counted else ""


def _joined(values) -> str:
    return "; ".join(sorted({v for v in values if v}))


def _day(moment) -> str:
    return "" if pd.isna(moment) else moment.date().isoformat()


def merge_repeats(people: pd.DataFrame) -> pd.DataFrame:
    """One row per (person, organisation key): the same person listed twice becomes one."""
    rows = []
    named = people[(people["person_key"] != "") & (people["org_key"] != "")]
    for (who, where), group in named.groupby(["person_key", "org_key"], sort=False):
        sources = set(group["Source"])
        event_ids = sorted({i for ids in group["Event_IDs"] for i in ids.split(", ") if i}, key=lambda i: (len(i), i))
        rows.append({
            "person_key": who, "org_key": where,
            "Name": display_name(group["Name"]), "Title": max(group["Title"], key=len),
            "Email": _most_common(group["Email"]), "Organisation": _most_common(group["Organisation"]),
            "Source": sources.pop() if len(sources) == 1 else "Both",
            "Event_IDs": ", ".join(event_ids),
        })
    return pd.DataFrame(rows)


def bucket(people: pd.DataFrame, meetings: pd.DataFrame, won: pd.DataFrame, left_tag: str) -> dict[str, pd.DataFrame]:
    """Sort each person into the three lead lists. A person can be in Paid and one other."""
    known_people = {
        pair: group for pair, group in meetings[meetings["contact_key"] != ""].groupby(["account_key", "contact_key"])
    }
    accounts = {key: group for key, group in meetings.groupby("account_key")}
    paying = {key: group for key, group in won.groupby("account_key")}
    personal, new_person, paid = [], [], []

    for person in people.to_dict("records"):
        shared = {k: person[k] for k in ("Name", "Title", "Email")} | {"Organisation": person["Organisation"]}
        tail = {"Source": person["Source"], "Event_IDs": person["Event_IDs"]}
        history = known_people.get((person["org_key"], person["person_key"]))
        account = accounts.get(person["org_key"])

        if history is not None:
            personal.append(shared | {
                "CRM_Account": _most_common(history["account_raw"]),
                "Past_Meetings": len(history),
                "Latest_Meeting": _day(history["date"].max()),
                "Past_Subjects": _joined(history["subject"]),
                # The CRM says this person has left. They are at the event all the same; a person should look.
                "Marked_As_Left": left_tag if history["left"].any() else "",
            } | tail)
        elif account is not None:
            contacts = account[account["contact_key"] != ""].groupby("contact_key").agg(
                name=("contact", _most_common), left=("left", "any"), latest=("date", "max"))
            contacts = contacts.sort_values("latest", ascending=False)
            if contacts.empty:
                status = "no contact recorded"
            elif not contacts["left"].any():
                status = "contacts still there"
            else:
                status = "all contacts have left" if contacts["left"].all() else "some contacts have left"
            new_person.append(shared | {
                "CRM_Account": _most_common(account["account_raw"]),
                "Account_Meetings": len(account),
                "Latest_Account_Meeting": _day(account["date"].max()),
                "Past_Contacts": "; ".join(
                    f"{c.name} ({left_tag})" if c.left else c.name for c in contacts.itertuples()),
                "Prior_Contact_Status": status,
                "Past_Subjects": _joined(account["subject"]),
            } | tail)

        deals = paying.get(person["org_key"])
        if deals is not None:
            by_source = deals.groupby("source")["amount"].sum().sort_values(ascending=False)
            paid.append(shared | {
                "CRM_Account": _most_common(deals["account_raw"]),
                "Total_Won_SEK": float(deals["amount"].sum()),
                "Won_Deals": len(deals),
                "Lead_Sources": "; ".join(s for s in by_source.index if s),
                "Latest_Close_Date": _day(deals["close"].max()),
            } | tail)

    def frame(rows: list[dict], order: list[str], ascending: list[bool]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).sort_values(order + ["Name"], ascending=ascending + [True]).reset_index(drop=True)

    return {
        PERSONAL: frame(personal, ["Latest_Meeting"], [False]),
        ACCOUNT: frame(new_person, ["Account_Meetings", "Latest_Account_Meeting"], [False, False]),
        PAID: frame(paid, ["Total_Won_SEK", "Won_Deals"], [False, False]),
    }


def org_match_audit(people: pd.DataFrame, meetings: pd.DataFrame, won: pd.DataFrame) -> pd.DataFrame:
    """Every organisation spelling in the programme and what, if anything, it matched."""
    meeting_accounts = meetings.groupby("account_key")["account_raw"].agg(_most_common)
    meeting_counts = meetings.groupby("account_key").size()
    won_accounts = won.groupby("account_key")["account_raw"].agg(_most_common)
    rows = []
    for (spelling, key), group in people[people["org_key"] != ""].groupby(["Organisation", "org_key"]):
        matched = key in meeting_accounts.index or key in won_accounts.index
        rows.append({
            "Organisation_As_Written": spelling,
            "Normalised": key,
            "Matched_Account": meeting_accounts.get(key, won_accounts.get(key, "")),
            "Match_Type": group["via"].iloc[0] if matched else "none",
            "People_At_Event": group["person_key"].nunique(),
            "Account_Meetings": int(meeting_counts.get(key, 0)),
            "Paying_Account": "Yes" if key in won_accounts.index else "No",
        })
    return pd.DataFrame(rows).sort_values(["Match_Type", "People_At_Event"], ascending=[True, False])


# ---------------------------------------------------------------------------
# The two phases
# ---------------------------------------------------------------------------

def _load_all(settings: Settings) -> tuple[AliasTable, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    aliases = AliasTable.load(settings.aliases)
    return (aliases, load_participants(settings, aliases),
            load_meetings(settings, aliases), load_won(settings, aliases))


def _fingerprint(settings: Settings) -> str:
    """Changes when the scraped people or either CRM export changes."""
    digest = hashlib.sha256()
    for path in (settings.output / "participants.xlsx", settings.meetings_file, settings.won_file):
        if not path.exists():
            raise SystemExit(f"{path} is missing. Run the steps again from: leadlist scrape")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def snapshot_file(settings: Settings):
    """The alias table as it stood when phase one stopped, kept so that the report can show what the review changed."""
    return settings.state / "aliases_at_phase_one.yaml"


# What phase two and the report write. Phase one clears them: they follow from an earlier phase one.
LATER_OUTPUTS = ("lead_lists.xlsx", "match_audit.xlsx", "run_report.html", "run_report.json",
                 "run_report_unreviewed.json")


def phase_one(settings: Settings) -> dict:
    aliases, people, meetings, won = _load_all(settings)
    merged = merge_repeats(people)
    collisions = find_collisions(people, meetings, won, aliases)
    to_review = [c for c in collisions if not c.decided]
    in_both = (set(people["org_key"]) & (set(meetings["account_key"]) | set(won["account_key"]))) - {""}

    stats = {
        "Programme rows as scraped": len(people),
        "Programme people after merging repeats": len(merged),
        "Programme rows with no organisation (left out)": int((people["org_key"] == "").sum()),
        "Programme organisations (normalised)": merged["org_key"].nunique(),
        "CRM meeting rows": len(meetings),
        "CRM accounts with meetings (normalised)": meetings["account_key"].nunique(),
        "CRM contacts": int(meetings[meetings["contact_key"] != ""].groupby(["account_key", "contact_key"]).ngroups),
        "CRM contacts marked as having left": int(
            meetings[meetings["left"]].groupby(["account_key", "contact_key"]).ngroups),
        "CRM accounts with won deals (normalised)": won["account_key"].nunique(),
        "Organisations on both sides": len(in_both),
        "Entries in the alias table": aliases.entry_count(),
        "Possible collisions": len(collisions),
        "Possible collisions to review": len(to_review),
    }
    audit = write_workbook(settings.output / "phase1_audit.xlsx", {
        "Possible_Collisions": collisions_frame(collisions),
        "Stats": pd.DataFrame(list(stats.items()), columns=["Measure", "Value"]),
    })
    settings.state.mkdir(parents=True, exist_ok=True)
    aliases.save(snapshot_file(settings))
    (settings.state / "phase1.json").write_text(json.dumps({
        "finished": datetime.now().isoformat(timespec="seconds"),
        "alias_entries": aliases.entry_count(),
        "inputs": _fingerprint(settings),
        "stats": stats,
    }, indent=1), encoding="utf-8")
    (settings.state / "phase2.json").unlink(missing_ok=True)
    for name in LATER_OUTPUTS:
        (settings.output / name).unlink(missing_ok=True)

    for label, value in stats.items():
        print(f"  {label:45s} {value}")
    rule = "=" * 70
    print(f"\n{rule}\nSTOP. Phase one is done and phase two has not started.\n")
    print(f"{len(to_review)} possible collisions are waiting for a person to read them:")
    print(f"  {audit}")
    print("For each one, decide whether the spellings are the same organisation,")
    print(f"and record it in {settings.aliases}")
    print("  by hand, or in the review screen:  leadlist review")
    print("When you have read them all:        leadlist phase2")
    print("Phase two will not run while any of them is undecided.")
    print(rule)
    return stats


def phase_two(settings: Settings, accept_undecided: bool = False) -> dict:
    marker = settings.state / "phase1.json"
    if not marker.exists():
        raise SystemExit("Phase one has not run. Run: leadlist phase1, then review, then phase2.")
    before = json.loads(marker.read_text(encoding="utf-8"))
    if before.get("inputs") != _fingerprint(settings):
        raise SystemExit("The scraped people or the CRM exports have changed since phase one. "
                         "Run: leadlist phase1, review what it lists, then phase2.")

    # Read everything again, so that the alias table as it stands now decides.
    aliases, people, meetings, won = _load_all(settings)
    merged = merge_repeats(people)
    still_open = [c for c in find_collisions(people, meetings, won, aliases) if not c.decided]
    if still_open and not accept_undecided:
        raise SystemExit(
            f"{len(still_open)} possible collisions have no decision yet, so phase two has not run.\n"
            "Decide them in the review screen (leadlist review) or in the alias table.\n"
            "To let them stand as the normaliser has them, say so: leadlist phase2 --accept-undecided")
    sheets = bucket(merged, meetings, won, settings.meetings.left_tag)
    lists = write_workbook(settings.output / "lead_lists.xlsx", sheets)
    audit = write_workbook(settings.output / "match_audit.xlsx", {
        "Org_Match_Audit": org_match_audit(people, meetings, won),
    })

    counts = {
        "people considered": len(merged),
        PERSONAL: len(sheets[PERSONAL]),
        ACCOUNT: len(sheets[ACCOUNT]),
        PAID: len(sheets[PAID]),
        "alias entries at phase one": before["alias_entries"],
        "alias entries now": aliases.entry_count(),
        "collisions left undecided": len(still_open),
    }
    (settings.state / "phase2.json").write_text(json.dumps({
        "finished": datetime.now().isoformat(timespec="seconds"), "counts": counts,
        "undecided": [asdict(c) for c in still_open],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"  alias table: {before['alias_entries']} entries at phase one, {aliases.entry_count()} now")
    for name in (PERSONAL, ACCOUNT, PAID):
        print(f"  {name:28s} {counts[name]:5d} rows")
    if still_open:
        print(f"  {len(still_open)} possible collisions were accepted undecided and stand as the normaliser has them")
    print(f"  wrote {lists}\n  wrote {audit}")
    return counts
