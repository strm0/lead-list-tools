"""The run report as one static HTML page, written next to the workbooks.

It is set as a reconciliation statement: what the program had, less what was
wrong, add what was missed, equals what the lists hold after the review.
"""
from __future__ import annotations

import html
from pathlib import Path

from .config import Settings
from .review_page import FONT_STACK

_STYLE = """
:root { --paper:#fcfdfb; --bar:#e2f0e0; --ink:#16241a; --soft:#3d4a40; --rule:#3f6b4a; --red:#b3261e;
        --sans:__FONT__;
        --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
* { box-sizing:border-box; }
::selection { background:var(--rule); color:var(--paper); }
html { background:var(--paper); color:var(--ink); font:17px/1.45 var(--sans); scrollbar-color:var(--rule) var(--bar); }
body { margin:0 auto; max-width:1180px; padding:44px 40px 72px; }
:focus-visible { outline:2px solid var(--rule); outline-offset:3px; }
a { color:var(--ink); text-decoration-color:var(--rule); text-underline-offset:4px; text-decoration-thickness:1.5px; }
a:hover { color:var(--rule); }
h1 { font-size:clamp(30px,4.4vw,42px); line-height:1.1; font-weight:700; letter-spacing:-.01em; margin:0; text-wrap:balance; }
h2 { font-size:21px; font-weight:700; margin:64px 0 4px; }
p { max-width:68ch; margin:0 0 12px; }
.dated { margin:8px 0 30px; max-width:none; }
.mock { color:var(--red); font-weight:600; }
.lead { color:var(--soft); font-size:16px; margin-bottom:14px; }

.scroll { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums lining-nums; }
th { font-size:15px; font-weight:700; text-align:right; padding:8px 18px; border-bottom:2px solid var(--rule);
     border-left:1px solid var(--rule); white-space:nowrap; }
th:first-child { text-align:left; border-left:0; }
td { padding:12px 18px; text-align:right; border-left:1px solid var(--rule); font-size:20px; white-space:nowrap; }
td:first-child { text-align:left; border-left:0; font-size:17px; white-space:normal; min-width:17em; }
td small { display:block; font-size:14px; color:var(--soft); }
tbody tr:nth-child(odd) td { background:var(--bar); }
tbody tr.total td { background:var(--paper); border-top:2px solid var(--rule); border-bottom:6px double var(--rule);
              font-weight:700; font-size:24px; }
tr.total td:first-child { font-size:18px; }
tr.total td small { font-weight:400; }
.scroll + .scroll { margin-top:36px; }
.bysheet { display:none; }
table.lists tr.total td { font-size:30px; }
table.lists tr.total td:first-child { font-size:20px; }
table.narrow { max-width:760px; }
.neg { color:var(--red); }
.tick { width:.72em; height:.72em; margin-left:.3em; vertical-align:-.02em; stroke:var(--red); fill:none;
        stroke-width:3.2; stroke-linecap:round; stroke-linejoin:round; }

details { border-bottom:1px solid var(--rule); max-width:960px; }
summary { cursor:pointer; padding:9px 0; font-size:16px; }
summary:hover { color:var(--rule); }
details ul { margin:0 0 14px; padding-left:22px; font-size:15px; color:var(--soft); }
li { margin:3px 0; overflow-wrap:anywhere; }
.files { list-style:none; margin:10px 0 0; padding:0; max-width:960px; border-top:2px solid var(--rule);
         border-bottom:6px double var(--rule); }
.files li { display:grid; grid-template-columns:17em 1fr; gap:4px 18px; padding:9px 0; margin:0;
            border-bottom:1px solid var(--rule); }
.files li:last-child { border-bottom:0; }
.files span { color:var(--soft); font-size:16px; }
code { font:15px var(--mono); }

@media (max-width:640px) {
  html { font-size:16px; } body { padding:28px 16px 56px; }
  td, th { padding:10px 12px; } td { font-size:18px; } td:first-child { min-width:12em; }
  table.lists tr.total td { font-size:24px; }
  .files li { grid-template-columns:1fr; }
  .ps { display:none; } .bysheet { display:block; margin-top:28px; }
  .bysheet td:first-child { min-width:0; overflow-wrap:anywhere; }
}
@media print { body { max-width:none; padding:0; } details { display:none; } h2 { break-after:avoid; } }
"""

_TICK = '<svg class="tick" viewBox="0 0 16 16" role="img" aria-label="agrees with the answer key"><path d="M2 9l4 5L14 2"/></svg>'

_SHEETS = {
    "Personal_Match": "a person already met",
    "Account_Match_New_Person": "a new person at an organisation already met",
    "Paid_Account_Match": "a contact at a paying organisation",
}
_FILES = (
    ("lead_lists.xlsx", "The three sheets."),
    ("match_audit.xlsx", "Every organisation in the programme and what it matched."),
    ("phase1_audit.xlsx", "The possible collisions a person reads at the stop."),
    ("municipality_contacts.xlsx", "Matched, rejected and parse issues."),
    ("participants.xlsx", "Everyone scraped from the programme."),
    ("congress.xlsx", "Speakers and sponsors from the congress API."),
)


def _num(value: int, negative: bool = False, tick: bool = False) -> str:
    """A figure. Deductions are set in brackets, the way a statement shows them."""
    text = f"{value:,}".replace(",", " ")
    if negative and value:
        return f'<td class="neg">({text})</td>'
    return f"<td>{text}{_TICK if tick else ''}</td>"


def _per_sheet(cells: list[str]) -> list[str]:
    """Mark every cell but the last as a per-sheet figure, which narrow screens move to a table of its own."""
    return [cell.replace("<td", '<td class="ps"', 1).replace('class="ps" class="neg"', 'class="ps neg"')
            for cell in cells[:-1]] + cells[-1:]


def _line(label: str, note: str, cells: list[str], total: bool = False) -> str:
    small = f"<small>{html.escape(note)}</small>" if note else ""
    return f'<tr{" class=total" if total else ""}><td>{html.escape(label).replace("_", "_<wbr>")}{small}</td>{"".join(cells)}</tr>'


def _statement(head: list[str], lines: list[str], css: str = "", per_sheet: list[str] = ()) -> str:
    cells = "".join(f"<th{' class=ps' if h in per_sheet else ''}>{html.escape(h)}</th>" for h in head)
    return (f'<div class="scroll"><table class="{css}"><thead><tr>{cells}</tr></thead>'
            f'<tbody>{"".join(lines)}</tbody></table></div>')


def _lists_statement(report: dict, unreviewed: dict | None) -> str:
    names = list(report["lists"])
    now = [report["lists"][n] for n in names]
    clean = [s["wrong"] == 0 and s["missed"] == 0 for s in now]
    total = lambda scores, field: sum(s[field] for s in scores)  # noqa: E731

    def across(scores: list[dict], field: str, **style) -> list[str]:
        return _per_sheet([_num(s[field], **style) for s in scores] + [_num(total(scores, field), **style)])

    head = [""] + names + ["All three sheets"]
    if unreviewed:
        before = [unreviewed["lists"][n] for n in names]
        # A person missed before the review and found by it is a row added; a wrong row is a row removed.
        lines = [
            _line("Rows as the program had them", "before the review", across(before, "rows")),
            _line("Less: wrong rows", "names that normalise alike and are different organisations",
                  across(before, "wrong", negative=True)),
            _line("Add: people missed", "spellings only a person can join", across(before, "missed")),
        ]
    else:
        lines = [
            _line("Rows that agree with the answer key", "right", across(now, "right")),
            _line("Add: wrong rows", "on the lists and should not be", across(now, "wrong")),
        ]
    if unreviewed and not all(clean):
        # The review did not get everything. Say so in the same arithmetic, instead of a total that does not add up.
        expected = [b["rows"] - b["wrong"] + b["missed"] for b in before]
        lines += [
            _line("Rows the answer key expects", "", _per_sheet([_num(n) for n in expected] + [_num(sum(expected))]), total=True),
            _line("Add: wrong rows still on the lists", "after the review", across(now, "wrong")),
            _line("Less: people still missed", "after the review", across(now, "missed", negative=True)),
        ]
    final = [_num(s["rows"], tick=ok) for s, ok in zip(now, clean)]
    final.append(_num(total(now, "rows"), tick=all(clean)))
    label = "Rows after the review" if unreviewed else "Rows on the lists"
    closing = [_line(label, "", _per_sheet(final), total=True)]
    if not unreviewed:
        closing.append(_line("People missed", "expected on a sheet and not there", across(now, "missed", negative=True)))
    wide = _statement(head, lines + closing, "lists", per_sheet=names)
    # The same figures turned on their side, for screens too narrow for five columns.
    if unreviewed:
        turned_head = ["", "Before", "Wrong", "Missed", "After"]
        turned = [_line(n, "", [_num(b["rows"]), _num(b["wrong"], negative=True), _num(b["missed"]), _num(s["rows"], tick=ok)])
                  for n, b, s, ok in zip(names, before, now, clean)]
    else:
        turned_head = ["", "Right", "Wrong", "Missed", "Rows"]
        turned = [_line(n, "", [_num(s["right"]), _num(s["wrong"]), _num(s["missed"], negative=True), _num(s["rows"], tick=ok)])
                  for n, s, ok in zip(names, now, clean)]
    return wide + f'<div class="bysheet">{_statement(turned_head, turned)}</div>'


def _scrape_statement(report: dict) -> str:
    scrape, flt = report["scrape"], report["filter"]
    counted = [
        _line("Mentions of a person", f"on {scrape['events']} event pages", [_num(scrape["mentions"])]),
        _line("Less: repeats of the same name and organisation", "", [_num(scrape["mentions"] - scrape["people"], negative=True)]),
        _line("People", "one row per name and organisation as written", [_num(scrape["people"])], total=True),
    ]
    if not flt:
        return (_statement(["", "People"], counted, "narrow")
                + '<p class="lead" style="margin-top:14px">The municipality filter was not run for this dataset.</p>')
    checked = f'<p class="lead">Checked against the answer key: {flt["right"]} right, {flt["wrong"]} wrong.</p>'
    merged = flt["merged_spellings"]
    adrift = scrape["people"] - flt["rejected"] - flt["parse_issues"] - merged - flt["matched"]
    lines = counted + [_line(f"Less: {reason}", "", [_num(number, negative=True)]) for reason, number in flt["rejected_by_reason"].items()]
    lines += [
        _line("Less: parse issues", "says kommun, names none on the official list", [_num(flt["parse_issues"], negative=True)]),
        _line("Less: one person under two spellings of a municipality", "", [_num(merged, negative=True)]),
    ]
    if adrift:
        lines.append(_line("Unaccounted for", "the lines above do not add up; something was lost", [_num(adrift, negative=True)]))
    lines.append(_line("Municipal contacts", f"in {flt['municipalities']} municipalities",
                       [_num(flt["matched"], tick=flt["wrong"] == 0 and not adrift)], total=True))
    return checked + _statement(["", "People"], lines, "narrow")


def _review_statement(report: dict) -> str:
    review = report["review"]
    known = review["possible_collisions"] - review["to_review_at_phase_one"]
    change = review["alias_entries_after"] - review["alias_entries_before"]
    return _statement(["", "Collisions"], [
        _line("Possible collisions listed by phase one", "", [_num(review["possible_collisions"])]),
        _line("Less: already decided in the alias table", "", [_num(known, negative=True)]),
        _line("Sent to a person at the stop", "", [_num(review["to_review_at_phase_one"])], total=True),
        _line("Left undecided when phase two ran", "counted again with the alias table as it stood then",
              [_num(review["left_undecided"], tick=review["left_undecided"] == 0)], total=True),
    ], "narrow") + _statement(["", "Entries"], [
        _line("Alias table at phase one", "", [_num(review["alias_entries_before"])]),
        _line("Add: entries written since" if change >= 0 else "Less: entries taken back since", "",
              [_num(abs(change), negative=change < 0)]),
        _line("Alias table now", "", [_num(review["alias_entries_after"])], total=True),
    ], "narrow")


def _notes(report: dict, unreviewed: dict | None) -> str:
    notes = []
    for when, source in (("before the review", unreviewed), ("after the review", report)):
        for name, score in (source or {"lists": {}})["lists"].items():
            for kind, items in (("wrong rows", score["wrong_rows"]), ("people missed", score["missed_people"])):
                if items:
                    lines = "".join(f"<li>{html.escape(item)}</li>" for item in items)
                    notes.append(f"<details><summary>{html.escape(name)}: {kind} {when}, {len(items)} shown</summary>"
                                 f"<ul>{lines}</ul></details>")
    if not notes:
        return ""
    return "<h2>Notes to the statement</h2><p class='lead'>The rows behind the deductions and additions above.</p>" + "".join(notes)


def render(report: dict) -> str:
    unreviewed = report.get("before_review")
    sheets = html.escape("; ".join(f"{name} is {what}" for name, what in _SHEETS.items()))
    explain = (
        "The first line is what the lists would hold with the alias table as it stood when phase one stopped. "
        "The last is what they hold now, after the review. "
        "A tick means every row agrees with the answer key: none wrong, none missed. Where there is no tick, "
        "the lines in between say what is still wrong or missing."
        if unreviewed else
        "A tick means every row agrees with the answer key: none wrong, none missed. "
        "The alias table has not changed since phase one, so there is no earlier state to compare with.")
    files = "".join(f'<li><a href="{name}">{name}</a><span>{html.escape(text)}</span></li>' for name, text in _FILES)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reconciliation of the run</title>
<style>{_STYLE.replace("__FONT__", FONT_STACK)}</style>
</head>
<body>
<h1>Reconciliation of the run</h1>
<p class="dated">Phase two finished {html.escape(report['finished'].replace('T', ' at ')[:19])}.
<span class="mock">Mock data: every person and organisation was generated.</span></p>

{_lists_statement(report, unreviewed)}
<p class="lead" style="margin-top:22px">{explain}</p>
<p class="lead">{sheets}.</p>

<h2>From the programme to the people</h2>
{_scrape_statement(report)}

<h2>The review</h2>
<p class="lead">Phase one stops here. Phase two runs when a person starts it.</p>
{_review_statement(report)}

{_notes(report, unreviewed)}

<h2>Schedules</h2>
<p class="lead">The workbooks this statement is drawn from, in the same folder.</p>
<ul class="files">{files}</ul>
</body>
</html>
"""


def write(settings: Settings, report: dict) -> Path:
    path = settings.output / "run_report.html"
    path.write_text(render(report), encoding="utf-8")
    return path
