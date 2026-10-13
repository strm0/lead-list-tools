"""Sort the scraped people by whether their organisation is a municipality.

Three sheets, so that what was left out can be checked as easily as what
was kept: Matched, Rejected (with the reason) and Parse_Issues.
"""
from __future__ import annotations

from collections import Counter

import pandas as pd

from .config import Settings
from .normalize import display_name
from .municipalities import MATCHED, PARSE_ISSUE, classify
from .workbook import read_sheet, write_workbook


def sort_people(people: pd.DataFrame) -> dict[str, pd.DataFrame]:
    verdicts = people["Organisation"].map(classify)
    people = people.assign(
        Status=verdicts.map(lambda v: v.status),
        Municipality=verdicts.map(lambda v: v.municipality),
        Reason=verdicts.map(lambda v: v.reason),
    )
    matched = people[people["Status"] == MATCHED]
    rows = []
    # One person under two spellings of the same municipality is one contact.
    for (_, municipality), group in matched.groupby([matched["Name"].str.lower(), "Municipality"], sort=False):
        emails = [e for e in group["Email"] if e]
        event_ids = sorted({i for ids in group["Event_IDs"] for i in ids.split(", ")}, key=lambda i: (len(i), i))
        rows.append({
            "Municipality": municipality, "Name": display_name(group["Name"]),
            "Title": max(group["Title"], key=len), "Email": emails[0] if emails else "",
            "Organisation_As_Written": "; ".join(dict.fromkeys(group["Organisation"])),
            "Event_IDs": ", ".join(event_ids),
        })
    columns = ["Municipality", "Name", "Title", "Email", "Organisation_As_Written", "Event_IDs"]
    plain = ["Name", "Title", "Email", "Organisation", "Reason", "Event_IDs"]
    return {
        "Matched": pd.DataFrame(rows, columns=columns).sort_values(["Municipality", "Name"]),
        "Rejected": people.loc[~people["Status"].isin([MATCHED, PARSE_ISSUE]), plain].sort_values(["Reason", "Organisation"]),
        "Parse_Issues": people.loc[people["Status"] == PARSE_ISSUE, plain],
    }


def run(settings: Settings) -> dict:
    source = settings.output / "participants.xlsx"
    if not source.exists():
        raise SystemExit("No participants yet. Run: leadlist scrape")
    sheets = sort_people(read_sheet(source, "All_Participants"))
    path = write_workbook(settings.output / "municipality_contacts.xlsx", sheets)
    reasons = Counter(sheets["Rejected"]["Reason"])
    counts = {
        "matched": len(sheets["Matched"]),
        "municipalities": sheets["Matched"]["Municipality"].nunique(),
        "rejected": len(sheets["Rejected"]),
        "parse issues": len(sheets["Parse_Issues"]),
        "rejected by reason": dict(reasons.most_common()),
    }
    print(f"  matched {counts['matched']} people in {counts['municipalities']} municipalities, "
          f"rejected {counts['rejected']}, parse issues {counts['parse issues']}")
    for reason, number in reasons.most_common():
        print(f"    {number:5d}  {reason}")
    print(f"  wrote {path}")
    return counts
