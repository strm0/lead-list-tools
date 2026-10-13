# lead-list-tools: handover

This file is the handover. It is written for whoever runs or changes the pipeline next, whether that is a person or Claude Code working for one. Read it before touching anything.

Everything in this repository runs on generated mock data. No real person or organisation belongs in it, in code, tests, fixtures, comments or commit messages.

## What the pipeline is for

It answers one question for a sales team: of the people named in an event programme, who do we already know, who is new at an organisation we know, and who works somewhere that has paid us before?

## How to run it

```
uv sync
uv run leadlist demo                    # everything, with a stand-in reviewer at the stop
uv run leadlist demo --stop-at-review   # everything up to the stop
uv run leadlist review                  # the review screen
uv run leadlist phase2                  # the lead lists and the run report; refuses while anything is undecided
```

Each step is also its own subcommand: `generate`, `serve-mock`, `scrape`, `scrape-congress`, `filter`, `phase1`, `review`, `phase2`, `report`. `uv run leadlist filter --self-test` checks the municipality rules. `uv run pytest` runs the tests.

## Layout

```
config/settings.toml        paths, mock data size, column names of the CRM exports
config/org_aliases.yaml     the alias table: what people have decided about spellings
src/leadlist/
  cli.py                    one entry point, one subcommand per step
  normalize.py              the normaliser and the alias table, shared by every pipeline
  municipalities.py         the 290 municipalities and the rules for recognising one
  municipality_filter.py    matched, rejected, parse issues
  scrape_programme.py       event pages to one row per person
  scrape_congress.py        congress API to speakers and sponsors
  crossref.py               phase one, phase two, the possible collisions
  review.py, review_page.py the review screen
  report.py, report_page.py the run report
  mock/                     the generator, the mock site and API, the demo's stand-in reviewer
work/                       everything generated or written; not in git
```

## The two phases, and why there are two

Phase one normalises both sides, lists every place where different spellings land on the same normalised name, writes `work/output/phase1_audit.xlsx`, prints STOP and exits. Phase two writes the lead lists.

The boundary is on purpose. Do not join the phases. A person reads the possible collisions and records each decision in the alias table. Phase two reads the table as it stands when it runs. It refuses to run while any collision is undecided, and refuses if the scraped people or the CRM exports have changed since phase one. Phase one deletes the lists and the report of any earlier run, and keeps a copy of the alias table as it stood, for the report's comparison.

`leadlist phase2 --accept-undecided` runs with collisions left open. They then stand as the normaliser has them, and the count is printed and shown in the run report. Use it knowingly; do not make it the default.

## Rules that must hold

- **No fuzzy matching, anywhere.** People match on lowercase and collapsed whitespace only. Organisations match on the normalised name, or because the alias table says so. If you are tempted to add a similarity score, add a row to the review screen instead.
- **No language model in the matching.** The same reason: a confident wrong match is the expensive mistake.
- **One normaliser.** Every comparison of organisations or people between the programme and the CRM goes through `normalize.py`, on both sides. Add rules there, not in another module.
- **Nothing is merged quietly.** Names that collide are matched as the normaliser has them and listed for review. `keep_apart` in the alias table separates the ones that are different organisations.
- **A decision covers the spellings it names.** A new spelling that normalises like a decided one is listed again. So is a spelling that looks like a kept-apart name without being one of its listed spellings: a person says which organisation it is.
- **The left tag is never dropped.** A CRM contact marked "(LEFT)", or with a note as in "(LEFT 2024)", has left the organisation. The word is set in `config/settings.toml`. The tag is taken off for matching and shown again in the output: in `Past_Contacts` on Account_Match_New_Person and in `Marked_As_Left` on Personal_Match.
- **Show what was left out.** Rejected rows and parse issues are written next to the matches, with the reason.

## The alias table

```yaml
aliases:
  Canonical Name:
    - a spelling that is the same organisation
keep_apart:
  - A name that looks like another once normalised, and is not
```

Spellings are compared after normalisation, so case, punctuation, "&" against "och" and company forms do not need lines of their own for matching. A name may be listed under another that is itself listed under a third; the order of the file does not matter. A table that lists one spelling under two names is refused, with the line named.

The review screen writes the same file. When it saves, it keeps every entry but not comments added by hand. Its undo will not overwrite a change made by hand in the meantime.

## How the normaliser works, in order

1. Strip and lowercase. Letters typed as two characters (o followed by combining dots) are read as one.
2. Write "&" as " och ".
3. Remove punctuation. It is removed, not replaced by a space.
4. Collapse whitespace.
5. If the name is listed under `keep_apart`, stop here: it keeps its own identity.
6. Drop company forms and similar words from both ends (`AFFIX_TOKENS`), unless fewer than three characters would be left.
7. Look the result up in the alias table.

## The municipality rules, in order

A municipality is named when a name on the official list is followed by "kommun" or "stad", with or without a genitive s, anywhere in the string. Two-word names are tried before one-word names, so the longest name wins.

1. Empty: rejected.
2. Names Region Gotland as whole words: matched as Gotland, the only region that is also a municipality.
3. Names more than one municipality: parse issue.
4. Names one municipality and also looks like a joint body (kommunalförbund, räddningstjänst, brandförsvar, arbetarekommun), a region or a company (AB, HB, KB): parse issue.
5. Names one municipality and nothing else of that kind: matched.
6. A joint body or a region with no municipality named: rejected.
7. Says kommun or stad but names none on the list: parse issue.
8. Anything else: rejected.

A parse issue is for a person to look at. The program does not pick, and spelling mistakes are not corrected.

## Text from outside

Everything scraped or read from an export is treated as hostile text.

- `workbook.write_workbook` stores any cell that starts with `=`, `+`, `-`, `@`, a tab or a line break as text with a leading quote, so that no spreadsheet program runs it as a formula. `read_sheet` takes that quote off again; `read_report`, which reads other people's exports, changes nothing. Write every workbook through `write_workbook`.
- `fetch.Fetcher` is given the address it was pointed at and refuses any other scheme, host or port, including through a redirect. Response size, page counts and loops have limits, named as constants at the top of each scraper.
- `normalize.tidy` removes control characters and invisible format characters before anything is compared or written.
- The review screen listens on 127.0.0.1 only, checks the Host header and a token only its own page holds, and sends headers that stop framing.
- The mock site listens on 127.0.0.1 only and shows a marker written by `generate`. `mock.server.running` uses a server already on the port only when the marker matches.

## The CRM exports

Report exports open with a title and filter notes, leave the first column empty, mark the sorted column with an arrow and print group labels once per group. `workbook.read_report` finds the header row by the column names in `config/settings.toml`. To read a differently labelled report, change the names there.

## The mock data

`leadlist generate` builds a seeded world and writes four things under `work/mock/`: a programme site, the data behind a congress API, two CRM exports, and `answer_key.json`. The pipeline never reads the answer key. Only the run report and the demo's stand-in reviewer do. The generator also writes the starting alias table from it: a third of the decisions, as if made for an earlier event.

Generating again clears everything under `work/`: the mock data, the scrape cache, the phase markers and the outputs. It leaves an existing alias table alone unless `--reset-aliases` is given. `leadlist demo` always starts from the dataset's own starting table. No municipality is ever in the mock CRM or the alias table: they are real names and stay on the programme side.

The stand-in reviewer joins short names to full ones as well. The audit workbook does not list those, since they share no normalised name. A person finds them in the review screen under "Matched nothing", or in `match_audit.xlsx`.

## The run report

`run_report.html` is set as a reconciliation statement. When the alias table has changed since phase one it compares: rows as the program had them, less wrong rows, add people missed, equals the rows the answer key expects. The "before" figures are worked out in memory from the table phase one saved. When nothing has changed it shows right, wrong and missed for the lists as they are. The figures are also written as `run_report.json` and `run_report_unreviewed.json`. After a complete review that is also the number of rows on the lists, and each figure gets a tick. After an incomplete one the statement goes on to say what is still wrong and still missing. Wrong and missed can be counted only because the data is generated.

Every row is scored. It is right only if the person is expected on that sheet, no other organisation's history is mixed into the match, and the person is not listed twice.

## Before pointing the scraper at a real site

Set `delay_seconds` in `config/settings.toml`. Read the site's terms. Keep real output out of this repository.
