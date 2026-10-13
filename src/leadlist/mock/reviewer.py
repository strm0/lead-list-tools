"""A stand-in for the person at the stop, for the demo only.

On real data a person reads the possible collisions and edits the alias
table. The demo has no person, so this plays one: it reads the answer key and
records what a careful reviewer would decide. Nothing outside the demo uses it.
"""
from __future__ import annotations

import json

from ..config import Settings
from ..crossref import find_collisions, load_meetings, load_participants, load_won
from ..normalize import AliasTable


def review_from_answer_key(settings: Settings) -> dict:
    key = json.loads(settings.truth_file.read_text(encoding="utf-8"))
    table = AliasTable.load(settings.aliases)
    before = table.entry_count()

    # Names that collide but are different organisations, and spellings only a person can join.
    table.add_keep_apart(key["review"]["keep_apart"])
    for canonical, variants in key["review"]["aliases"].items():
        table.add_alias(canonical, variants)

    # Then confirm every remaining collision whose spellings all belong to one organisation.
    people, meetings, won = (load(settings, table) for load in (load_participants, load_meetings, load_won))
    confirmed = 0
    for collision in find_collisions(people, meetings, won, table):
        owners = {key["spellings"].get(spelling.strip()) for spelling in collision.spellings}
        if not collision.decided and len(owners) == 1 and None not in owners:
            table.add_alias(key["organisations"][owners.pop()]["name"], collision.spellings)
            confirmed += 1

    table.save(settings.aliases)
    return {"entries before": before, "entries after": table.entry_count(),
            "kept apart": len(key["review"]["keep_apart"]), "collisions confirmed": confirmed}
