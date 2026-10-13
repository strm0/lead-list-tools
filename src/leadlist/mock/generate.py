"""Generate the mock world the pipeline runs on.

One seeded run writes four things under work/mock/:

  site/programme/   a paginated event programme as static HTML, one page per event
  api/              the data behind a small congress JSON API
  crm/              two report exports: meetings and won deals
  answer_key.json   which spelling belongs to which organisation, who is who

The pipeline never reads the answer key. Only the run report does, to count
how many matches came out right.

The data is messy on purpose, in the ways the pipeline exists to handle:
"&" against "och", legal suffixes, casing, stray punctuation, abbreviations,
"stad" against "kommun", one person listed twice, and pairs of organisations
whose names normalise alike but must be kept apart.
"""
from __future__ import annotations

import html
import json
import random
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from ..config import Settings
from ..municipalities import official_names
from ..normalize import AliasTable, base_key
from ..review_page import FONT_STACK
from . import names as N

# Invented dates, chosen to fall on no real event's week that could be found.
EVENT_DAYS = [date(2027, 4, 19) + timedelta(days=offset) for offset in range(5)]
CONGRESS_DAYS = [date(2027, 8, 12) + timedelta(days=offset) for offset in range(4)]
EVENTS_PER_INDEX_PAGE = 50


@dataclass(eq=False)
class Org:
    id: str
    kind: str
    name: str
    easy: list[str] = field(default_factory=list)   # the normaliser reconciles these with `name`
    hard: list[str] = field(default_factory=list)   # these need a line in the alias table
    municipality: str = ""
    filter_status: str = "rejected"
    programme_spellings: list[str] = field(default_factory=list)
    crm_spellings: list[str] = field(default_factory=list)
    people: list["Person"] = field(default_factory=list)


@dataclass(eq=False)
class Person:
    id: str
    name: str
    title: str
    org: Org

    @property
    def email(self) -> str:
        given, _, family = self.name.partition(" ")
        return f"{N.slug(given)}.{N.slug(family)}@{N.slug(self.org.name)[:24]}.example"


class World:
    def __init__(self, seed: int, events: int) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.event_count = events
        self.orgs: list[Org] = []
        self.people: list[Person] = []
        self.names = N.NameMaker(self.rng)
        self.keep_apart: list[list[str]] = []
        self.events: list[dict] = []
        self.meetings: list[dict] = []
        self.deals: list[dict] = []
        self.meeting_accounts: set[str] = set()
        self.meeting_contacts: set[str] = set()
        self.won_accounts: set[str] = set()
        self.congress: dict = {}
        self._roots = N.org_roots(self.rng, set(official_names()))

    # -- organisations ------------------------------------------------------

    def _org(self, kind: str, name: str, **extra) -> Org:
        org = Org(id=f"o{len(self.orgs) + 1:03d}", kind=kind, name=name, **extra)
        self.orgs.append(org)
        return org

    def _person(self, org: Org, name: str | None = None) -> Person:
        person = Person(
            id=f"p{len(self.people) + 1:04d}",
            name=name or self.names.new(),
            title=self.rng.choice(N.JOB_TITLES),
            org=org,
        )
        self.people.append(person)
        org.people.append(person)
        return person

    def build_orgs(self, scale: float) -> None:
        rng, root = self.rng, self._roots.pop

        for _ in range(round(90 * scale)):
            base = f"{root()} {rng.choice(N.SECTORS)}"
            short = base.split()[0]
            self._org("company", f"{base} AB", hard=[short], easy=[
                base, f"{base} AB".upper(), f"{base} Ab.", f"{base} Sverige AB", f"{base} AB (publ)",
            ])
        for _ in range(round(12 * scale)):
            a, b, trade = root(), root(), rng.choice(N.PROFESSIONS)
            self._org("partnership", f"{a} & {b} {trade}", hard=[f"{a} {b}"], easy=[
                f"{a} och {b} {trade}", f"{a}&{b} {trade} AB", f"{a} & {b} {trade}".lower(),
            ])
        for _ in range(round(22 * scale)):
            name = root()
            self._org("association", f"Föreningen {name}", hard=[f"{name}förbundet"], easy=[
                name, f"föreningen {name}", f"Föreningen {name}.",
            ])
        for topic in rng.choices(N.INSTITUTE_TOPICS, k=round(14 * scale)):
            name = root()
            full = f"{name}institutet för {topic}"
            self._org("institute", full, hard=[f"{name}inst. för {topic}"], easy=[
                full.upper(), f"{full}.", f"Stiftelsen {full}",
            ])

        # Pairs that normalise to the same key and are different organisations.
        for _ in range(max(2, round(4 * scale))):
            name = root()
            foundation = self._org("kept apart", f"Stiftelsen {name}", easy=[f"STIFTELSEN {name.upper()}"])
            company = self._org("kept apart", f"{name} AB", easy=[f"{name} ab"])
            self.keep_apart.append([foundation.name, company.name])

        municipal = rng.sample(sorted(set(official_names().values()) - {"Gotland"}), round(44 * scale))
        for place in municipal:
            owner = place if place[-1] in "aeiouyåäös" else f"{place}s"
            self._org("municipality", f"{owner} kommun", municipality=place, filter_status="matched",
                      easy=[f"{owner} kommun".upper(), f"{owner} Kommun."],
                      hard=[f"{owner} stad", f"{rng.choice(N.DEPARTMENTS)}, {owner} kommun"])
        self._org("municipality", "Region Gotland", municipality="Gotland", filter_status="matched",
                  easy=["REGION GOTLAND"], hard=["Gotlands kommun"])

        for _ in range(round(6 * scale)):
            name = root()
            self._org("region", rng.choice([f"Region {name}", f"{name}regionen"]))
        for _ in range(round(5 * scale)):
            name = root()
            self._org("joint body", rng.choice([f"{name} kommunalförbund", f"Räddningstjänsten {name}"]))
        for _ in range(round(4 * scale)):
            self._org("unknown kommun", f"{root()}s kommun", filter_status="parse_issue")

        for org in self.orgs:
            for _ in range(rng.randint(2, 6)):
                self._person(org)

    # -- who spells what ----------------------------------------------------

    def assign_spellings(self) -> None:
        rng = self.rng
        matchable = [o for o in self.orgs if o.kind in ("company", "partnership", "association", "institute")]
        # Municipalities are real names, so they stay out of the CRM: no meetings, deals or alias lines for them.
        in_crm = set(o.id for o in rng.sample(matchable, round(len(matchable) * 0.42)))

        for org in self.orgs:
            if org.kind == "kept apart":
                continue
            if org.id in in_crm:
                pattern = rng.choices(["same", "easy", "hard"], weights=[35, 40, 25])[0]
                if pattern == "same" or not org.easy:
                    org.programme_spellings, org.crm_spellings = [org.name], [org.name]
                elif pattern == "easy":
                    org.programme_spellings = rng.sample(org.easy, min(2, len(org.easy)))
                    org.crm_spellings = [org.name] + rng.sample(org.easy, 1) * rng.randint(0, 1)
                else:
                    org.programme_spellings = [org.hard[0]] + [org.name] * rng.randint(0, 1)
                    org.crm_spellings = [org.name]
            else:
                pool = [org.name] + org.easy + org.hard
                org.programme_spellings = rng.sample(pool, min(len(pool), rng.randint(1, 2)))

        for index, (foundation_name, company_name) in enumerate(self.keep_apart):
            foundation = next(o for o in self.orgs if o.name == foundation_name)
            company = next(o for o in self.orgs if o.name == company_name)
            foundation.crm_spellings = [foundation.name]
            foundation.programme_spellings = foundation.easy if index % 2 else []
            company.programme_spellings = [company.name] + company.easy
            company.crm_spellings = [company.name] if index % 2 else []

    # -- the programme ------------------------------------------------------

    def build_events(self) -> None:
        rng = self.rng
        present = [o for o in self.orgs if o.programme_spellings]
        weights = [3 if o.kind == "municipality" else 2 if o.crm_spellings else 1 for o in present]
        for number in range(self.event_count):
            organiser = rng.choices(present, weights)[0]
            guests = rng.choices(present, weights, k=rng.randint(1, 3))
            participants = [rng.choice(o.people) for o in [organiser] + guests]
            contacts = rng.sample(organiser.people, min(len(organiser.people), rng.randint(1, 2)))
            if rng.random() < 0.4:
                contacts[0] = participants[0]  # the same person in both roles
            topic = rng.choice(N.EVENT_TOPICS)
            day = rng.choice(EVENT_DAYS)
            self.events.append({
                "id": str(1001 + number),
                "title": rng.choice(N.EVENT_OPENERS).format(topic=topic, Topic=topic.capitalize()),
                "day": day.isoformat(),
                "time": f"{rng.randint(8, 17):02d}:{rng.choice(['00', '30'])}",
                "organiser": self._spell(organiser),
                "participants": [self._occurrence(p) for p in dict.fromkeys(participants)],
                "contacts": [self._occurrence(p, with_email=True) for p in dict.fromkeys(contacts)],
            })

    def _spell(self, org: Org) -> str:
        spellings = org.programme_spellings
        # The first spelling is the usual one; the others turn up now and then.
        return spellings[0] if self.rng.random() < 0.7 else self.rng.choice(spellings)

    def _occurrence(self, person: Person, with_email: bool = False) -> dict:
        name = person.name
        roll = self.rng.random()
        if roll < 0.03:
            name = name.upper()
        elif roll < 0.06:
            name = name.replace(" ", "  ")
        return {
            "person": person.id, "name": name, "title": person.title,
            "organisation": self._spell(person.org),
            "email": person.email if with_email and self.rng.random() < 0.85 else "",
        }

    # -- the CRM ------------------------------------------------------------

    def build_crm(self, left_tag: str) -> None:
        rng = self.rng
        staff = [self.names.new() for _ in range(5)]
        accounts = [o for o in self.orgs if o.crm_spellings]
        won_only = set(o.id for o in rng.sample(accounts, max(1, len(accounts) // 8)))

        for org in accounts:
            if org.id in won_only:
                continue
            self.meeting_accounts.add(org.id)
            contacts = [p for p in org.people if rng.random() < 0.55]
            contacts += [self._person(org) for _ in range(rng.randint(0, 2))]  # never at the event
            for person in contacts:
                self.meeting_contacts.add(person.id)
                left = rng.random() < 0.12
                note = rng.choice(["", " 2024", ", retired"])
                contact = f"{person.name} ({left_tag}{note})" if left else person.name
                for _ in range(rng.randint(1, 5)):
                    self.meetings.append(self._meeting(org, contact, staff))
            if not contacts or rng.random() < 0.2:
                self.meetings.append(self._meeting(org, "", staff))  # a meeting logged without a contact

        for org in accounts:
            if org.id in won_only or rng.random() < 0.35:
                self.won_accounts.add(org.id)
                for number in range(rng.randint(1, 4)):
                    self.deals.append({
                        "source": rng.choice(N.LEAD_SOURCES),
                        "account": rng.choice(org.crm_spellings),
                        "owner": rng.choice(staff),
                        "opportunity": f"{org.name} {2021 + number}",
                        "amount": rng.randrange(40_000, 480_000, 5_000),
                        "close": date(2021 + number, rng.randint(1, 12), rng.randint(1, 28)),
                    })

        # A namesake: the same full name at an account the event person does not work for.
        at_event = [p for o in self.orgs if o.programme_spellings and not o.crm_spellings for p in o.people]
        hosts = [o for o in accounts if o.id in self.meeting_accounts]
        for person in rng.sample(at_event, min(5, len(at_event))):
            namesake = self._person(rng.choice(hosts), name=person.name)
            self.meeting_contacts.add(namesake.id)
            self.meetings.append(self._meeting(namesake.org, namesake.name, staff))

    def _meeting(self, org: Org, contact: str, staff: list[str]) -> dict:
        rng = self.rng
        return {
            "assigned": rng.choice(staff),
            "subject": rng.choice(N.MEETING_SUBJECTS),
            "date": date(rng.randint(2020, 2026), rng.randint(1, 5), rng.randint(1, 28)),
            "account": rng.choice(org.crm_spellings),
            "contact": contact,
        }

    # -- the congress -------------------------------------------------------

    def build_congress(self) -> None:
        rng = self.rng
        sponsors = []
        for number in range(40):
            name = f"{self._roots.pop()} {rng.choice(N.CONGRESS_FIELDS)}"
            sponsors.append({
                "id": 500 + number,
                "name": f"{name} {rng.choice(N.CONGRESS_LEGAL)}",
                "short": name,
                "tier": N.CONGRESS_TIERS[min(3, number // 10 + rng.randint(0, 1))],
            })
        institutions = [f"{self._roots.pop()} {kind}" for kind in ("University Hospital", "Institute") for _ in range(12)]
        speakers = []
        for _ in range(150):
            given, _, family = self.names.new().partition(" ")
            sponsor = rng.choice(sponsors) if rng.random() < 0.3 else None
            affiliation = rng.choice([sponsor["name"], sponsor["short"], sponsor["name"].upper()]) if sponsor else rng.choice(institutions)
            speakers.append({"first_name": given, "last_name": family,
                             "country": rng.choice(N.CONGRESS_COUNTRIES), "affiliation": affiliation})
        placeholder = [{"first_name": text, "last_name": "", "country": "", "affiliation": ""}
                       for text in N.PLACEHOLDER_SPEAKERS]

        sessions = []
        for number in range(70):
            day = rng.choice(CONGRESS_DAYS)
            start = rng.randint(8, 17)
            kind = rng.choice(N.CONGRESS_SESSION_TYPES)
            sponsored = kind == "Satellite symposium" or rng.random() < 0.25
            talks = [{"title": f"{rng.choice(N.CONGRESS_TOPICS)}, part {part + 1}",
                      "speaker": rng.choice(placeholder) if rng.random() < 0.06 else rng.choice(speakers)}
                     for part in range(rng.randint(2, 4))]
            sessions.append({
                "id": 7000 + number,
                "title": f"{rng.choice(N.CONGRESS_TOPICS)}: {kind.lower()} {number + 1}",
                "type": kind,
                "starts_at": f"{day.isoformat()}T{start:02d}:00",
                "ends_at": f"{day.isoformat()}T{start + 1:02d}:30",
                "sponsor_ids": [rng.choice(sponsors)["id"]] if sponsored else [],
                "chairs": rng.sample(speakers, rng.randint(1, 2)),
                "talks": talks,
            })
        self.congress = {"sponsors": sponsors, "sessions": sessions}

        # A few sponsors are already accounts in the CRM, under a slightly different spelling.
        staff = sorted({m["assigned"] for m in self.meetings}) or ["Mock Owner"]
        for sponsor in rng.sample(sponsors, 8):
            org = self._org("sponsor", sponsor["name"], crm_spellings=[f"{sponsor['short']} AB"])
            self.meeting_accounts.add(org.id)
            self.meetings.append(self._meeting(org, "", staff))


# ---------------------------------------------------------------------------
# Writing the files
# ---------------------------------------------------------------------------

# The mock site wears the same look as the report and the review screen.
_SITE_STYLE = """
:root { --paper:#fcfdfb; --bar:#e2f0e0; --ink:#16241a; --soft:#3d4a40; --rule:#3f6b4a; --red:#b3261e; }
* { box-sizing:border-box; }
::selection { background:var(--rule); color:var(--paper); }
html { background:var(--paper); color:var(--ink); font:17px/1.45 __FONT__; }
body { margin:0 auto; max-width:1040px; padding:28px 40px 72px; }
:focus-visible { outline:2px solid var(--rule); outline-offset:3px; }
a { color:var(--ink); text-decoration-color:var(--rule); text-underline-offset:4px; text-decoration-thickness:1.5px; }
a:hover { color:var(--rule); }
.top { display:flex; justify-content:space-between; gap:8px 24px; flex-wrap:wrap; padding-bottom:12px;
       border-bottom:2px solid var(--rule); font-size:16px; }
.notice { color:var(--red); font-weight:700; margin:0; }
h1 { font-size:clamp(28px,4.2vw,40px); line-height:1.12; margin:36px 0 8px; text-wrap:balance; }
h2 { font-size:19px; margin:40px 0 0; padding-bottom:8px; border-bottom:2px solid var(--rule); }
.when, .organiser { margin:0; color:var(--soft); font-variant-numeric:lining-nums tabular-nums; }
.organiser { color:var(--ink); font-size:19px; }
ul { list-style:none; margin:0; padding:0; }
.person, .events li { display:grid; gap:2px 24px; padding:11px 16px; margin:0; border-bottom:1px solid var(--rule); }
.person { grid-template-columns:1.1fr 1fr 1.4fr; }
.contacts .person { grid-template-columns:1.1fr 1fr 1.4fr 1.6fr; }
.person:nth-of-type(odd), .events li:nth-child(odd) { background:var(--bar); }
.person .name { font-weight:700; }
.person .title { color:var(--soft); }
.person .email { overflow-wrap:anywhere; font-size:16px; }
.events { border-top:2px solid var(--rule); margin-top:20px; }
.events li { grid-template-columns:1fr auto; align-items:baseline; }
.events span { color:var(--soft); font-size:16px; font-variant-numeric:lining-nums tabular-nums; white-space:nowrap; }
.pager { margin-top:24px; display:flex; justify-content:space-between; }
@media (max-width:700px) {
  body { padding:20px 16px 56px; }
  .person, .contacts .person, .events li { grid-template-columns:1fr; }
}
""".replace("__FONT__", FONT_STACK)

_PAGE = """<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<div class="top"><a href="index.html">Program</a>
<p class="notice">Mock programme. Every person and organisation here is invented.</p></div>
{body}
</body>
</html>
"""


def _person_html(tag: str, occurrence: dict) -> str:
    e = html.escape
    parts = [
        f'<span class="name">{e(occurrence["name"])}</span>',
        f'<span class="title">{e(occurrence["title"])}</span>',
        f'<span class="org">{e(occurrence["organisation"])}</span>',
    ]
    if occurrence["email"]:
        parts.append(f'<a class="email" href="mailto:{e(occurrence["email"])}">{e(occurrence["email"])}</a>')
    return f'<{tag} class="person">{" ".join(parts)}</{tag}>'


def _write_site(world: World, settings: Settings) -> None:
    folder = settings.mock / "site" / "programme"
    folder.mkdir(parents=True)
    e = html.escape
    for event in world.events:
        participants = "\n".join(_person_html("li", p) for p in event["participants"])
        contacts = "\n".join(_person_html("p", c) for c in event["contacts"])
        body = f"""<article class="event" data-event-id="{event['id']}">
<h1>{e(event['title'])}</h1>
<p class="when"><time datetime="{event['day']}T{event['time']}">{event['day']} {event['time']}</time></p>
<p class="organiser">{e(event['organiser'])}</p>
<section class="participants">
<h2>Medverkande</h2>
<ul>
{participants}
</ul>
</section>
<section class="contacts">
<h2>Kontaktperson</h2>
{contacts}
</section>
</article>"""
        (folder / f"event-{event['id']}.html").write_text(
            _PAGE.format(title=e(event["title"]), style=_SITE_STYLE, body=body), encoding="utf-8")

    pages = [world.events[i:i + EVENTS_PER_INDEX_PAGE]
             for i in range(0, len(world.events), EVENTS_PER_INDEX_PAGE)]
    for number, page in enumerate(pages, start=1):
        links = "\n".join(
            f'<li><a class="event-link" href="event-{ev["id"]}.html">{e(ev["title"])}</a>'
            f'<span>{ev["day"]} {ev["time"]}</span></li>' for ev in page)
        earlier = "index.html" if number == 2 else f"page-{number - 1}.html"
        back = f'<a href="{earlier}">Föregående sida</a>' if number > 1 else "<span></span>"
        following = f'<a rel="next" href="page-{number + 1}.html">Nästa sida</a>' if number < len(pages) else ""
        body = (f"<h1>Program, sida {number} av {len(pages)}</h1>\n<ul class=\"events\">\n{links}\n</ul>\n"
                f'<div class="pager">{back}{following}</div>')
        name = "index.html" if number == 1 else f"page-{number}.html"
        (folder / name).write_text(_PAGE.format(title="Program", style=_SITE_STYLE, body=body), encoding="utf-8")


def _set_widths(sheet, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _write_meetings(world: World, settings: Settings) -> None:
    columns = settings.meetings
    book = Workbook()
    sheet = book.active
    sheet.title = "Meetings report"
    for line in ("Report: Meetings, all time", "Generated from mock data", None, "Filtered by: Type equals Meeting", None):
        sheet.append([None, line])
    sheet.append([None, "Assigned", columns.subject, columns.date, columns.account, columns.contact])
    for row in sorted(world.meetings, key=lambda m: (m["date"], m["account"], m["contact"])):
        sheet.append([None, row["assigned"], row["subject"], row["date"], row["account"], row["contact"] or None])
    sheet.append([])
    sheet.append([None, f"Total records: {len(world.meetings)}"])
    _set_widths(sheet, [3, 24, 26, 13, 44, 36])
    settings.meetings_file.parent.mkdir(parents=True, exist_ok=True)
    book.save(settings.meetings_file)


def _write_won(world: World, settings: Settings) -> None:
    columns = settings.won
    book = Workbook()
    sheet = book.active
    sheet.title = "Won opportunities"
    for line in ("Report: Opportunities, closed won", "Generated from mock data", None):
        sheet.append([None, line])
    # The export marks its sort column with an arrow and prints group labels once.
    sheet.append([None, columns.source, f"{columns.account}  ↑", "Opportunity Owner",
                  columns.opportunity, columns.amount, columns.close_date])
    previous_source = previous_account = None
    for deal in sorted(world.deals, key=lambda d: (d["source"], d["account"], d["close"])):
        new_source = deal["source"] != previous_source
        new_account = new_source or deal["account"] != previous_account
        sheet.append([None, deal["source"] if new_source else None, deal["account"] if new_account else None,
                      deal["owner"], deal["opportunity"], deal["amount"], deal["close"]])
        previous_source, previous_account = deal["source"], deal["account"]
    sheet.append([])
    sheet.append([None, "Grand total", None, None, None, sum(d["amount"] for d in world.deals)])
    _set_widths(sheet, [3, 22, 44, 24, 48, 14, 13])
    book.save(settings.won_file)


def _review_answers(world: World) -> dict:
    """The alias lines and keep-apart names a careful reviewer would end up with."""
    aliases = {}
    for org in world.orgs:
        if org.kind == "kept apart" or not org.crm_spellings:
            continue
        used = dict.fromkeys(org.crm_spellings + org.programme_spellings)
        variants = [s for s in used if base_key(s) != base_key(org.name)]
        distinct = list({base_key(v): v for v in variants}.values())
        if distinct:
            aliases[org.name] = distinct
    return {"aliases": aliases, "keep_apart": [name for pair in world.keep_apart for name in pair]}


def _answer_key(world: World, review: dict) -> dict:
    spellings = {}
    for org in world.orgs:
        for spelling in [org.name] + org.programme_spellings + org.crm_spellings:
            owner = spellings.setdefault(spelling.strip(), org.id)
            assert owner == org.id, f"spelling {spelling!r} is used by two organisations"
    at_event = {occurrence["person"] for event in world.events
                for occurrence in event["participants"] + event["contacts"]}
    return {
        "note": "Generated. The pipeline does not read this file; the run report does.",
        "seed": world.seed,
        "organisations": {o.id: {"name": o.name, "kind": o.kind, "municipality": o.municipality,
                                 "filter_status": o.filter_status} for o in world.orgs},
        "spellings": spellings,
        "people": {p.id: {"name": p.name, "organisation": p.org.id} for p in world.people},
        "at_event": sorted(at_event),
        "meeting_accounts": sorted(world.meeting_accounts),
        "meeting_contacts": sorted(world.meeting_contacts),
        "won_accounts": sorted(world.won_accounts),
        "review": review,
    }


def generate(settings: Settings, reset_aliases: bool = False) -> dict:
    """Build the world from the seed and write every mock file. Returns counts."""
    world = World(settings.seed, settings.events)
    world.build_orgs(scale=max(0.25, min(1.5, settings.events / 300)))
    world.assign_spellings()
    world.build_events()
    world.build_crm(settings.meetings.left_tag)
    world.build_congress()

    # A new world makes everything read from the old one stale.
    for folder in (settings.mock, settings.cache, settings.state, settings.output):
        if folder.exists():
            shutil.rmtree(folder)
    _write_site(world, settings)
    _write_meetings(world, settings)
    _write_won(world, settings)
    api = settings.mock / "api"
    api.mkdir(parents=True)
    (api / "congress.json").write_text(json.dumps(world.congress, ensure_ascii=False, indent=1), encoding="utf-8")

    # A mark of this very dataset. The mock server shows it, and a server found already running is used only if it matches.
    (settings.mock / "run_marker.txt").write_text(secrets.token_hex(8), encoding="utf-8")

    review = _review_answers(world)
    settings.truth_file.write_text(
        json.dumps(_answer_key(world, review), ensure_ascii=False, indent=1), encoding="utf-8")

    wrote_aliases = reset_aliases or not settings.aliases.exists()
    if wrote_aliases:
        # The table as it might look before this event: some spellings already known.
        known = dict(sorted(review["aliases"].items())[: len(review["aliases"]) // 3])
        AliasTable(aliases=known).save(settings.aliases)

    return {
        "seed": world.seed, "events": len(world.events), "organisations": len(world.orgs),
        "people": len(world.people), "meetings": len(world.meetings), "won deals": len(world.deals),
        "congress sessions": len(world.congress["sessions"]),
        "alias table": "written" if wrote_aliases else "kept as it was (--reset-aliases writes this dataset's own)",
    }
