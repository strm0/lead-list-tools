"""The run report: what a run produced, and how much of it was right.

On real data nobody knows the right answer for every row. Here the data is
generated, so the answer key says which organisation each spelling belongs to
and who has met whom. The report compares the lead lists with it and counts
right, wrong and missed.
"""
from __future__ import annotations

import json
from collections import Counter

from .config import Settings
from .crossref import (
    ACCOUNT, PAID, PERSONAL, bucket, load_meetings, load_participants, load_won, merge_repeats, snapshot_file,
)
from .normalize import AliasTable, person_key
from .workbook import read_sheet


class AnswerKey:
    def __init__(self, settings: Settings) -> None:
        key = json.loads(settings.truth_file.read_text(encoding="utf-8"))
        self.organisations = key["organisations"]
        self._spellings = key["spellings"]
        self._person_at = {(person_key(p["name"]), p["organisation"]): pid for pid, p in key["people"].items()}
        self.people = key["people"]
        at_event = set(key["at_event"])
        contacts, meeting_accounts, won_accounts = (
            set(key["meeting_contacts"]), set(key["meeting_accounts"]), set(key["won_accounts"]))
        employer = {pid: key["people"][pid]["organisation"] for pid in at_event}
        self.expected = {
            PERSONAL: {p for p in at_event if p in contacts},
            ACCOUNT: {p for p in at_event if p not in contacts and employer[p] in meeting_accounts},
            PAID: {p for p in at_event if employer[p] in won_accounts},
        }

    def organisation(self, spelling: str) -> str:
        return self._spellings.get(spelling.strip(), "")

    def person(self, name: str, organisation_id: str) -> str:
        return self._person_at.get((person_key(name), organisation_id), "")

    def describe(self, person_id: str) -> str:
        person = self.people[person_id]
        return f"{person['name']}, {self.organisations[person['organisation']]['name']}"


def _score_lists(sheets: dict, aliases: AliasTable, settings: Settings, key: AnswerKey) -> dict:
    """Every row is right or wrong. A row is right only when all of this holds:

    the person is one the answer key expects on that sheet, every CRM row
    behind the match belongs to the organisation the person is at (so no
    other organisation's history is mixed in), and the person is not on the
    sheet already.
    """
    def owners(frame) -> dict:
        return {account: {key.organisation(raw) for raw in raws}
                for account, raws in frame.groupby("account_key")["account_raw"]}

    meeting_owners, won_owners = owners(load_meetings(settings, aliases)), owners(load_won(settings, aliases))
    behind = {PERSONAL: meeting_owners, ACCOUNT: meeting_owners, PAID: won_owners}
    scores = {}
    for sheet in (PERSONAL, ACCOUNT, PAID):
        rows = sheets[sheet]
        right, wrong = set(), []
        for row in rows.to_dict("records"):
            written = key.organisation(row["Organisation"])
            person = key.person(row["Name"], written)
            accounts = behind[sheet].get(aliases.key(row["Organisation"]).key, set())
            if not written or accounts - {written}:
                problem = "another organisation's history"
            elif person not in key.expected[sheet]:
                problem = "not expected on this sheet"
            elif person in right:
                problem = "listed twice"
            else:
                right.add(person)
                continue
            wrong.append(f"{row['Name']}, {row['Organisation']}  matched to  {row['CRM_Account']}  ({problem})")
        missed = sorted(key.expected[sheet] - right)
        scores[sheet] = {
            "rows": len(rows), "right": len(right), "wrong": len(wrong), "missed": len(missed),
            "wrong_rows": wrong[:25], "missed_people": [key.describe(p) for p in missed[:25]],
        }
    return scores


def _before_review(settings: Settings, aliases: AliasTable, key: AnswerKey) -> dict | None:
    """What the lists would have held with the alias table as it stood when phase one stopped.

    Worked out in memory from the table phase one saved; nothing is written to
    the lists. None when the table has not changed since, as there is then
    nothing to compare.
    """
    saved = snapshot_file(settings)
    if not saved.exists():
        return None
    earlier = AliasTable.load(saved)
    if (earlier.aliases, sorted(earlier.keep_apart)) == (aliases.aliases, sorted(aliases.keep_apart)):
        return None
    people = merge_repeats(load_participants(settings, earlier))
    sheets = bucket(people, load_meetings(settings, earlier), load_won(settings, earlier), settings.meetings.left_tag)
    return {"alias_entries": earlier.entry_count(), "lists": _score_lists(sheets, earlier, settings, key)}


def _score_filter(settings: Settings, key: AnswerKey) -> dict | None:
    path = settings.output / "municipality_contacts.xlsx"
    if not path.exists():
        return None                      # the filter is a step of its own and may not have been run
    matched, rejected, issues = (read_sheet(path, name) for name in ("Matched", "Rejected", "Parse_Issues"))
    right = wrong = 0
    for row in matched.to_dict("records"):
        truth = key.organisations.get(key.organisation(row["Organisation_As_Written"].split("; ")[0]), {})
        ok = truth.get("municipality") == row["Municipality"]
        right, wrong = right + ok, wrong + (not ok)
    for frame, status in ((rejected, "rejected"), (issues, "parse_issue")):
        for spelling in frame["Organisation"]:
            ok = key.organisations.get(key.organisation(spelling), {}).get("filter_status") == status
            right, wrong = right + ok, wrong + (not ok)
    spellings_merged = int(matched["Organisation_As_Written"].str.split("; ").str.len().sum()) - len(matched)
    return {
        "merged_spellings": spellings_merged,
        "matched": len(matched), "municipalities": int(matched["Municipality"].nunique()),
        "rejected": len(rejected), "parse_issues": len(issues),
        "rejected_by_reason": dict(Counter(rejected["Reason"]).most_common()),
        "right": int(right), "wrong": int(wrong),
    }


def build(settings: Settings) -> dict:
    """Gather the numbers of the latest run. Needs phase two to have run."""
    phase2 = settings.state / "phase2.json"
    if not phase2.exists():
        raise SystemExit("Nothing to report yet. Phase two has not run since the latest phase one.")
    for needed in (settings.output / "lead_lists.xlsx", settings.output / "participants.xlsx", settings.truth_file):
        if not needed.exists():
            raise SystemExit(f"{needed} is missing, so there is nothing to score. Run phase two again: leadlist phase2")
    key = AnswerKey(settings)
    aliases = AliasTable.load(settings.aliases)
    lists = {sheet: read_sheet(settings.output / "lead_lists.xlsx", sheet) for sheet in (PERSONAL, ACCOUNT, PAID)}
    participants = settings.output / "participants.xlsx"
    phase1 = json.loads((settings.state / "phase1.json").read_text(encoding="utf-8"))
    phase1_stats = phase1["stats"]
    phase2_state = json.loads(phase2.read_text(encoding="utf-8"))
    report = {
        "finished": phase2_state["finished"],
        "phase_one": phase1["finished"],      # which phase one this run follows from
        "scrape": {
            "events": len(read_sheet(participants, "Events")),
            "mentions": len(read_sheet(participants, "Occurrences")),
            "people": len(read_sheet(participants, "All_Participants")),
        },
        "filter": _score_filter(settings, key),
        "review": {
            "possible_collisions": phase1_stats["Possible collisions"],
            "to_review_at_phase_one": phase1_stats["Possible collisions to review"],
            "left_undecided": phase2_state["counts"]["collisions left undecided"],
            "alias_entries_before": phase2_state["counts"]["alias entries at phase one"],
            "alias_entries_after": phase2_state["counts"]["alias entries now"],
        },
        "lists": _score_lists(lists, aliases, settings, key),
        "before_review": _before_review(settings, aliases, key),
    }
    (settings.output / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    comparison = settings.output / "run_report_unreviewed.json"
    if report["before_review"]:
        comparison.write_text(json.dumps(report["before_review"], ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        comparison.unlink(missing_ok=True)
    return report


def print_summary(report: dict) -> None:
    scrape, flt, review = report["scrape"], report["filter"], report["review"]
    print(f"  scraped      {scrape['events']} events, {scrape['mentions']} mentions, {scrape['people']} people")
    if flt:
        print(f"  filter       {flt['matched']} matched, {flt['rejected']} rejected, {flt['parse_issues']} parse issues"
              f"   (checked against the answer key: {flt['right']} right, {flt['wrong']} wrong)")
    else:
        print("  filter       not run")
    print(f"  review       {review['to_review_at_phase_one']} collisions went to review, "
          f"{review['left_undecided']} left undecided; alias table "
          f"{review['alias_entries_before']} to {review['alias_entries_after']} entries")
    before = report["before_review"]
    if before:
        print("\n  with the alias table as it stood at phase one:")
        _print_scores(before["lists"])
        print("\n  with the alias table as it stands now:")
    _print_scores(report["lists"])


def _print_scores(lists: dict) -> None:
    print(f"  {'list':28s} {'rows':>6s} {'right':>6s} {'wrong':>6s} {'missed':>7s}")
    for name, score in lists.items():
        print(f"  {name:28s} {score['rows']:6d} {score['right']:6d} {score['wrong']:6d} {score['missed']:7d}")
