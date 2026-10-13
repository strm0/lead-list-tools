"""Building blocks for invented people and organisations.

Nothing here is a list of real people or real organisations. The surnames
and the organisation names are made up from parts, and any resemblance to a
real one is accidental. Given names are ordinary ones, since a given name
alone points at nobody.
"""
from __future__ import annotations

import random
import unicodedata

GIVEN_NAMES = [
    "Anna", "Erik", "Maria", "Lars", "Karin", "Johan", "Sara", "Anders", "Emma", "Per",
    "Lena", "Mikael", "Eva", "Nils", "Kristina", "Jonas", "Ingrid", "Fredrik", "Elin", "Magnus",
    "Sofia", "Daniel", "Linnea", "Peter", "Hanna", "Mattias", "Ida", "Henrik", "Amanda", "Gustav",
    "Malin", "Oskar", "Jenny", "Martin", "Cecilia", "Andreas", "Frida", "Tobias", "Helena", "Simon",
    "Maja", "Viktor", "Ebba", "Axel", "Klara", "Emil", "Alva", "Rasmus", "Tove", "Isak",
    "Amira", "Yusuf", "Leila", "Samir", "Mira", "Darius", "Nadia", "Elias", "Selma", "Noah",
]

SURNAME_ROOTS = [
    "Vark", "Tosk", "Mirr", "Jäld", "Fenk", "Ögv", "Tjalv", "Gorv", "Nisp", "Hulv",
    "Kväs", "Drim", "Pjäl", "Stuv", "Ymb", "Blork", "Skrim", "Knuv", "Trasp", "Glyv",
    "Fjorv", "Snarp", "Brylt", "Dålk", "Purv", "Lysk", "Hjorp", "Krumv", "Sölp", "Mjugg",
]
SURNAME_ENDINGS = [
    "ström", "qvist", "berg", "lund", "ander", "elius",
    "gren", "man", "dahl", "stedt", "feldt", "vall",
]

# Deliberately unlikely: every one of the 400 roots these give was looked up and found unused.
ORG_SYLLABLES_FIRST = [
    "Skvurm", "Tjyrv", "Pjusk", "Gnyv", "Kvärp", "Fnurv", "Dvälp", "Mjösk", "Hjylp", "Snörv",
    "Krymt", "Vrysk", "Glömv", "Tvösk", "Spjurk", "Njovk", "Stjyv", "Bjölk", "Drögv", "Knävd",
]
ORG_SYLLABLES_LAST = [
    "ömta", "ygdo", "ålke", "uxne", "ärpe", "övle", "yrgu", "ämsko", "ödru", "ulpte",
    "ivnö", "ågmy", "yglu", "ätvu", "upnö", "öppra", "yskle", "ådvy", "ebmu", "irpsa",
]

SECTORS = [
    "Energi", "Bygg", "Data", "Logistik", "Konsult", "Fastigheter", "Media", "Vård",
    "Skog", "Finans", "Teknik", "Miljö", "Livsmedel", "Transport", "Utbildning",
]
PROFESSIONS = ["Arkitekter", "Advokater", "Revisorer", "Ingenjörer"]
INSTITUTE_TOPICS = [
    "kustfrågor", "samhällsbygge", "skogsbruk", "folkbildning", "digital omställning",
    "vattenvård", "landsbygdsutveckling", "arbetsliv", "energiomställning", "stadsodling",
]
DEPARTMENTS = [
    "Utbildningsförvaltningen", "Socialförvaltningen", "Kulturförvaltningen",
    "Stadsbyggnadskontoret", "Näringslivsenheten", "Äldreomsorgen",
]
JOB_TITLES = [
    "Kommunalråd", "Förvaltningschef", "Verksamhetsutvecklare", "Projektledare",
    "Kommunikationschef", "VD", "Hållbarhetschef", "Utredare", "Generalsekreterare",
    "Ordförande", "Affärsutvecklare", "Strateg", "Enhetschef", "Forskare", "Rådgivare",
]
EVENT_OPENERS = [
    "Hur klarar vi {topic}?", "Framtidens {topic}", "{Topic} i praktiken",
    "Vem tar ansvar för {topic}?", "Samtal om {topic}", "{Topic}: vad händer nu?",
]
EVENT_TOPICS = [
    "kompetensförsörjning", "energiomställning", "äldreomsorg", "digitalisering",
    "bostadsbyggande", "psykisk hälsa", "kollektivtrafik", "skolans framtid",
    "beredskap", "cirkulär ekonomi", "vattenförsörjning", "integration",
]
MEETING_SUBJECTS = [
    "Introductory meeting", "Follow-up", "Proposal review", "Workshop planning",
    "Partnership renewal", "Budget discussion", "Kick-off", "Evaluation",
]
LEAD_SOURCES = ["Initiative North", "Initiative Harbour", "Initiative Meadow", "Inbound"]

CONGRESS_FIELDS = ["Therapeutics", "Diagnostics", "Biosciences", "Medical", "Pharma", "Labs"]
CONGRESS_LEGAL = ["Inc", "Ltd", "GmbH", "AB", "A/S"]
CONGRESS_TIERS = ["Platinum", "Gold", "Silver", "Bronze"]
CONGRESS_COUNTRIES = [
    "Sweden", "Norway", "Denmark", "Finland", "Germany", "France", "Spain",
    "Italy", "Netherlands", "United Kingdom", "Canada", "Japan",
]
CONGRESS_SESSION_TYPES = ["Plenary", "Symposium", "Workshop", "Satellite symposium", "Poster talk"]
CONGRESS_TOPICS = [
    "Early detection", "Long-term follow-up", "Trial design", "Patient pathways",
    "Data sharing", "Emerging therapies", "Quality of life", "Rare conditions",
]
PLACEHOLDER_SPEAKERS = ["TBA", "Faculty"]


def slug(text: str) -> str:
    """ASCII, lowercase, no spaces: for file names and e-mail addresses."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(ch for ch in folded.lower() if ch.isalnum())


def surnames() -> list[str]:
    return [root + ending for root in SURNAME_ROOTS for ending in SURNAME_ENDINGS]


def org_roots(rng: random.Random, avoid: set[str]) -> list[str]:
    """Every syllable pair, shuffled, minus anything that is a municipality name."""
    roots = [a + b for a in ORG_SYLLABLES_FIRST for b in ORG_SYLLABLES_LAST]
    roots = [root for root in roots if root.lower() not in avoid]
    rng.shuffle(roots)
    return roots


class NameMaker:
    """Hands out full names, each one once, so that a name identifies a person."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._surnames = surnames()
        self._used: set[str] = set()

    def new(self) -> str:
        for _ in range(10_000):
            name = f"{self._rng.choice(GIVEN_NAMES)} {self._rng.choice(self._surnames)}"
            if name not in self._used:
                self._used.add(name)
                return name
        raise RuntimeError("ran out of names: add surname roots or endings")
