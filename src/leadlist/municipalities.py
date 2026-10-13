"""The municipality filter: is this organisation one of Sweden's 290 municipalities?

The list in data/municipalities.txt is the official one from Statistics Sweden.
A name counts only when it is followed by "kommun" or "stad", so an
organisation that merely has a place in its name is not swept in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

from .normalize import tidy

MATCHED, REJECTED, PARSE_ISSUE = "matched", "rejected", "parse_issue"

# Bodies that carry the word but are not a municipality.
NOT_A_MUNICIPALITY = ("arbetarekommun", "brandförsvar", "kommunalförbund", "räddningstjänst")
# Gotland is the only region that is also a municipality.
_REGION_GOTLAND = re.compile(r"\bregion gotland\b")
# A municipality's name inside a company's name is not the municipality.
COMPANY_FORMS = frozenset({"ab", "aktiebolag", "hb", "kb"})
# Official names that are not the place name plus an ending.
IRREGULAR_FORMS = {"falu": "Falun"}

_WORD = re.compile(r"[\w\-]+")
# The word for a municipality in every form it takes in running text.
KOMMUN_FORMS = frozenset("kommun" + ending for ending in ("", "s", "en", "ens", "er", "erna", "ernas"))


@dataclass(frozen=True)
class Verdict:
    status: str
    municipality: str
    reason: str


@cache
def official_names() -> dict[str, str]:
    """Lowercased name -> official spelling."""
    text = resources.files("leadlist.data").joinpath("municipalities.txt").read_text("utf-8")
    names = {line.strip().lower(): line.strip() for line in text.splitlines() if line.strip()}
    names.update(IRREGULAR_FORMS)
    return names


def _lookup(candidate: str) -> str | None:
    names = official_names()
    lowered = candidate.lower()
    if lowered in names:
        return names[lowered]
    if lowered.endswith("s") and lowered[:-1] in names:  # genitive: "Lunds kommun"
        return names[lowered[:-1]]
    return None


def _named_municipalities(text: str) -> list[tuple[int, str]]:
    """Every municipality named before "kommun" or "stad", with the length matched."""
    words = _WORD.findall(text)
    found = []
    for position, word in enumerate(words):
        if word.lower() not in ("kommun", "stad"):
            continue
        for width in (2, 1):  # the longest name wins: "Östra Göinge" before anything shorter
            if position - width < 0:
                continue
            candidate = " ".join(words[position - width:position])
            official = _lookup(candidate)
            if official:
                found.append((len(candidate), official))
                break
    return found


def classify(organisation: object) -> Verdict:
    """Matched, rejected, or a parse issue for a person to look at.

    A string is matched only when it is plainly one municipality. Anything
    that names a municipality and also looks like something else (a region,
    a joint body, a company, a second municipality) is a parse issue: the
    program does not pick.
    """
    text = tidy(organisation).rstrip(".").strip()
    if not text:
        return Verdict(REJECTED, "", "no organisation given")
    lowered = text.lower()
    words = {word.lower() for word in _WORD.findall(text)}

    other = next((word for word in NOT_A_MUNICIPALITY if word in lowered), "")
    region = lowered.startswith("region ") or any(word.endswith("regionen") for word in words)
    company = bool(words & COMPANY_FORMS)
    named = sorted({name for _, name in _named_municipalities(text)})

    if _REGION_GOTLAND.search(lowered) and not company and len(named) <= 1:
        return Verdict(MATCHED, "Gotland", "special case: region and municipality in one")
    if len(named) > 1:
        return Verdict(PARSE_ISSUE, "", f"names more than one municipality ({', '.join(named)})")
    if named:
        doubt = (f"a joint body ({other})" if other else "a region" if region else "a company" if company else "")
        if doubt:
            return Verdict(PARSE_ISSUE, "", f"names {named[0]} but also looks like {doubt}")
        return Verdict(MATCHED, named[0], "name on the official list")
    if other:
        return Verdict(REJECTED, "", f"not a municipality ({other})")
    if region:
        return Verdict(REJECTED, "", "a region, not a municipality")
    if words & KOMMUN_FORMS or "stad" in words:
        return Verdict(PARSE_ISSUE, "", "says kommun or stad but names none on the official list")
    return Verdict(REJECTED, "", "no municipality named")


# (organisation, expected status, expected municipality)
SELF_TEST_CASES = [
    ("Umeå kommun", MATCHED, "Umeå"),
    ("Stockholms stad", MATCHED, "Stockholm"),
    ("GÖTEBORGS STAD", MATCHED, "Göteborg"),
    ("Borås stad", MATCHED, "Borås"),
    ("Falu kommun", MATCHED, "Falun"),
    ("Östra Göinge kommun", MATCHED, "Östra Göinge"),
    ("Upplands Väsby kommun", MATCHED, "Upplands Väsby"),
    ("Utbildningsförvaltningen, Karlstads kommun", MATCHED, "Karlstad"),
    ("Kulturskolan Lunds kommun.", MATCHED, "Lund"),
    ("Malmö stad", MATCHED, "Malmö"),
    ("Region Gotland", MATCHED, "Gotland"),
    ("Region Spjurkupnö", REJECTED, ""),
    ("Drögvövleregionen", REJECTED, ""),
    ("Spjurkupnö kommunalförbund", REJECTED, ""),
    ("Räddningstjänsten Drögvövle", REJECTED, ""),
    ("Uppsala Tjyrvklubb", REJECTED, ""),
    ("Stockholm Stadsdrögvarna", REJECTED, ""),
    ("", REJECTED, ""),
    ("Region Gotlands Fnurvklubb", REJECTED, ""),
    ("Spjurkupnö kommun", PARSE_ISSUE, ""),
    ("Spjurkupnö stad", PARSE_ISSUE, ""),
    ("Kommunernas samverkansgrupp", PARSE_ISSUE, ""),
    ("Ale kommun och Alingsås kommun", PARSE_ISSUE, ""),
    ("Region Spjurkupnö och Lunds kommun", PARSE_ISSUE, ""),
    ("Räddningstjänsten, Lunds kommun", PARSE_ISSUE, ""),
    ("Friskolan i Lunds kommun AB", PARSE_ISSUE, ""),
]


def self_test() -> bool:
    """Run the classifier over known cases and print each verdict."""
    all_passed = True
    for organisation, status, municipality in SELF_TEST_CASES:
        verdict = classify(organisation)
        passed = (verdict.status, verdict.municipality) == (status, municipality)
        all_passed = all_passed and passed
        mark = "ok  " if passed else "FAIL"
        print(f"{mark} {verdict.status:11s} {verdict.municipality:15s} {organisation!r}  ({verdict.reason})")
    print(f"\n{'All' if all_passed else 'NOT all'} {len(SELF_TEST_CASES)} cases passed.")
    return all_passed
