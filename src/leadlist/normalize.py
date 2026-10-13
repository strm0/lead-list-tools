"""One normaliser and one alias table, shared by every pipeline.

Organisation names are compared on a normalised form and nothing else: there is
no fuzzy matching anywhere. Two spellings are the same organisation when they
normalise to the same key, or when a person has said so in the alias table.
The same function runs on both sides of every comparison.
"""
from __future__ import annotations

import copy
import os
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Words that say what kind of body an organisation is, not which one. Grouped by what they are.
LEGAL_FORMS = {"ab", "aktiebolag", "as", "gmbh", "inc", "ltd", "oy", "publ"}
BRANCH_WORDS = {"group", "holding", "sverige", "sweden"}
BODY_WORDS = {"förening", "föreningen", "stiftelsen", "the"}
AFFIX_TOKENS = frozenset(LEGAL_FORMS | BRANCH_WORDS | BODY_WORDS)
# An affix is left in place when removing it would leave less than this.
MIN_REMAINING = 3

# Marks that separate or decorate a name without being part of it. They are removed, not turned into spaces.
_DROPPED_MARKS = str.maketrans("", "", "\"'(),-./:;")
_INVISIBLE = {"Cf", "Cs"}      # format characters (zero-width, direction marks, soft hyphen) and lone surrogates


def tidy(value: object) -> str:
    """Any cell value as plain text: nothing for a missing value, and nothing a reader cannot see.

    Control characters become spaces, invisible format characters vanish, a
    letter typed as two characters becomes one, and runs of whitespace collapse.
    """
    if value is None or (isinstance(value, float) and value != value):   # a float that is not itself is NaN
        return ""
    text = unicodedata.normalize("NFC", str(value))
    visible = []
    for character in text:
        category = unicodedata.category(character)
        if category == "Cc":
            visible.append(" ")
        elif category not in _INVISIBLE:
            visible.append(character)
    return " ".join("".join(visible).split())


def clean(text: object) -> str:
    """Lowercase, write "&" as "och", drop punctuation, collapse whitespace."""
    value = tidy(text).lower().replace("&", " och ").translate(_DROPPED_MARKS)
    return " ".join(value.split())


def strip_affixes(cleaned: str) -> str:
    """Drop affix words from both ends of an already cleaned name."""
    tokens = cleaned.split()
    while tokens:
        if tokens[-1] in AFFIX_TOKENS and len(" ".join(tokens[:-1])) >= MIN_REMAINING:
            tokens.pop()
        elif tokens[0] in AFFIX_TOKENS and len(" ".join(tokens[1:])) >= MIN_REMAINING:
            tokens.pop(0)
        else:
            break
    return " ".join(tokens)


def base_key(text: object) -> str:
    """The normalised form before the alias table is consulted."""
    return strip_affixes(clean(text))


def person_key(text: object) -> str:
    """People match on lowercase and collapsed whitespace only."""
    return tidy(text).lower()


def display_name(spellings) -> str:
    """Of several ways a person's name was written, the one to show.

    The most common, passing over an all-capitals version when there is another.
    """
    counted = Counter(s for s in spellings if s)
    return min(counted, key=lambda name: (name.isupper(), -counted[name]), default="")


def split_left_tag(contact: object, tag: str) -> tuple[str, bool]:
    """Separate a closing "(TAG ...)" from a contact name.

    Returns the name without the tag and whether the tag was there. The tag
    marks a contact who has left the organisation; it may carry a note, as in
    "(TAG 2024)". Brackets anywhere but the end are part of the name.
    """
    value = tidy(contact)
    if not value.endswith(")"):
        return value, False
    opening = value.rfind("(")
    inside = value[opening + 1:-1].strip() if opening >= 0 else ""
    rest = inside[len(tag):]
    if tag and inside.lower().startswith(tag.lower()) and not rest[:1].isalnum():
        return value[:opening].strip(), True
    return value, False


@dataclass(frozen=True)
class OrgKey:
    key: str
    via: str  # "direct", "alias" or "kept apart"


@dataclass
class AliasTable:
    """What people have decided about spellings.

    aliases:    canonical name -> spellings that are the same organisation.
    keep_apart: names that normalise alike but are different organisations.
                Each one keeps its own identity and skips affix stripping.

    A decision covers the spellings it names and no others. A new spelling
    that happens to normalise like a decided one is still shown to a person.
    """
    aliases: dict[str, list[str]] = field(default_factory=dict)
    keep_apart: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._index()

    def _index(self) -> None:
        self.conflicts: list[str] = []
        self._apart = {clean(name) for name in self.keep_apart if clean(name)}
        self._decided = set(self._apart)
        self._exact: dict[str, str] = {}
        self._display: dict[str, str] = {}
        listed_under: dict[str, str] = {}     # base of a spelling -> base of the name it is listed under
        owner: dict[str, str] = {}            # the same, as the name a person wrote, for messages

        # Sorted, so that the order of the file never changes what the table means.
        for canonical in sorted(self.aliases):
            variants = [v for v in (self.aliases[canonical] or []) if clean(v)]
            canonical_clean = clean(canonical)
            if not canonical_clean:
                continue
            self._decided.add(canonical_clean)
            self._decided.update(clean(v) for v in variants)
            if canonical_clean in self._apart:
                # Variants of a kept-apart name are matched on their exact
                # cleaned form, so they cannot pull its neighbour in with them.
                target = "=" + canonical_clean
                for variant in variants:
                    variant_clean = clean(variant)
                    if variant_clean in self._apart and variant_clean != canonical_clean:
                        self.conflicts.append(f"'{variant}' is kept apart and also listed as a spelling of '{canonical}'")
                    elif self._exact.setdefault(variant_clean, target) != target:
                        self.conflicts.append(f"'{variant}' is listed under two kept-apart names")
            else:
                target = strip_affixes(canonical_clean)
                for variant in variants:
                    base = base_key(variant)
                    if base == target:
                        continue
                    if listed_under.setdefault(base, target) != target:
                        self.conflicts.append(
                            f"'{variant}' is listed under both '{owner[base]}' and '{canonical}'")
                    owner.setdefault(base, canonical)
            self._display[target] = canonical

        # A name listed under another, which is listed under a third, ends up at the third.
        self._by_base: dict[str, str] = {}
        for base in listed_under:
            seen, target = {base}, listed_under[base]
            while target in listed_under:
                if target in seen:
                    self.conflicts.append(f"'{owner[base]}' and its spellings are listed under each other in a circle")
                    break
                seen.add(target)
                target = listed_under[target]
            self._by_base[base] = target

    def key(self, raw: object) -> OrgKey:
        cleaned = clean(raw)
        if not cleaned:
            return OrgKey("", "direct")
        if cleaned in self._apart:
            return OrgKey("=" + cleaned, "kept apart")
        if cleaned in self._exact:
            return OrgKey(self._exact[cleaned], "alias")
        base = strip_affixes(cleaned)
        target = self._by_base.get(base, base)
        return OrgKey(target, "alias" if target != base else "direct")

    def knows(self, raw: object) -> bool:
        """True when a person has ruled on this very spelling (case and punctuation aside)."""
        return clean(raw) in self._decided

    def kept_apart_lookalikes(self, raw: object) -> list[str]:
        """Kept-apart names this undecided spelling normalises like.

        "Hjylpödru AB (publ)" looks like the kept-apart "Hjylpödru AB" and like
        "Stiftelsen Hjylpödru". The program cannot say which it is, so it asks.
        """
        cleaned = clean(raw)
        if not cleaned or cleaned in self._decided:
            return []
        base = strip_affixes(cleaned)
        return [name for name in self.keep_apart if strip_affixes(clean(name)) == base]

    def canonical_name(self, key: str) -> str:
        return self._display.get(key, "")

    def entry_count(self) -> int:
        return len(self.aliases) + len(self.keep_apart)

    def add_alias(self, canonical: str, variants: list[str]) -> None:
        before = copy.deepcopy(self.aliases)
        existing = self.aliases.setdefault(canonical, [])
        for variant in variants:
            if variant != canonical and variant not in existing:
                existing.append(variant)
        self._index()
        if self.conflicts:
            problem = self.conflicts[0]
            self.aliases = before
            self._index()
            raise ValueError(problem)

    def add_keep_apart(self, names: list[str]) -> None:
        for name in names:
            if clean(name) and clean(name) not in {clean(n) for n in self.keep_apart}:
                self.keep_apart.append(name)
        self._index()

    @classmethod
    def load(cls, path: Path) -> "AliasTable":
        if not path.exists():
            return cls()
        try:
            with open(path, encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as problem:
            where = getattr(problem, "problem_mark", None)
            line = f", near line {where.line + 1}" if where else ""
            raise SystemExit(f"{path} is not valid YAML{line}. Fix it by hand and run the step again.") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("aliases") or {}, dict) \
                or not isinstance(raw.get("keep_apart") or [], list):
            raise SystemExit(f"{path} should hold 'aliases' (name: list of spellings) and 'keep_apart' (a list).")
        aliases = {
            str(name): [str(v) for v in (variants if isinstance(variants, list) else [variants] if variants else [])]
            for name, variants in (raw.get("aliases") or {}).items()
        }
        table = cls(aliases=aliases, keep_apart=[str(n) for n in (raw.get("keep_apart") or [])])
        if table.conflicts:
            listing = "\n".join(f"  {conflict}" for conflict in table.conflicts)
            raise SystemExit(f"{path} contradicts itself:\n{listing}\nFix it by hand and run the step again.")
        return table

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = yaml.safe_dump(
            {"aliases": self.aliases, "keep_apart": self.keep_apart},
            allow_unicode=True, sort_keys=True, default_flow_style=False, width=120,
        )
        # Written beside the file and moved into place, so a reader never sees half a table.
        draft = path.with_name(path.name + ".tmp")
        draft.write_text(ALIAS_FILE_HEADER + body, encoding="utf-8")
        os.replace(draft, path)


ALIAS_FILE_HEADER = """\
# The alias table. A person edits this after reading the possible collisions
# that phase one lists. Nothing here is guessed by the program.
#
# aliases:     a canonical name, then the spellings that are the same organisation.
# keep_apart:  names that look alike once normalised but are different organisations.
#
# Spellings are compared after normalisation, so case, punctuation, "&" against
# "och" and legal suffixes such as AB do not need separate lines.
#
# The review screen (leadlist review) writes this file too. When it saves, it
# keeps every entry and this header, but not other comments added by hand.

"""
