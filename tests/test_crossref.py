"""The matcher, on a handful of hand-made rows. Every person and organisation is invented."""
import pandas as pd

from leadlist.crossref import ACCOUNT, PAID, PERSONAL, bucket, find_collisions, merge_repeats
from leadlist.normalize import AliasTable, person_key

LEFT_TAG = "LEFT"


def _people(rows, aliases):
    frame = pd.DataFrame(rows, columns=["Name", "Organisation"]).assign(
        Title="Strateg", Email="", Source="Participant", Event_IDs="1001")
    frame["person_key"] = frame["Name"].map(person_key)
    frame["org_key"] = frame["Organisation"].map(lambda raw: aliases.key(raw).key)
    frame["via"] = frame["Organisation"].map(lambda raw: aliases.key(raw).via)
    return frame


def _meetings(rows, aliases):
    frame = pd.DataFrame(rows, columns=["account_raw", "contact", "left"]).assign(
        subject="Follow-up", date=pd.Timestamp("2025-03-04"))
    frame["contact_key"] = frame["contact"].map(person_key)
    frame["account_key"] = frame["account_raw"].map(lambda raw: aliases.key(raw).key)
    return frame


def _won(rows, aliases):
    frame = pd.DataFrame(rows, columns=["account_raw", "source", "amount"]).assign(close=pd.Timestamp("2024-11-01"))
    frame["account_key"] = frame["account_raw"].map(lambda raw: aliases.key(raw).key)
    return frame


def _run(aliases=None):
    aliases = aliases or AliasTable()
    people = _people([
        ("Anna Varkström", "Bjölkärpe Energi"),          # known person, account spelled without AB
        ("ANNA  VARKSTRÖM", "Bjölkärpe Energi AB"),      # the same person listed twice
        ("Erik Toskelius", "BJÖLKÄRPE ENERGI AB"),       # new person at a known account
        ("Sara Mirrqvist", "Dvälpyrgu Data AB"),          # nobody has met this organisation
        ("Nils Fenkberg", "Hjylpödru AB"),                # looks like the foundation below, is not
        ("Karin Jäldlund", "Mjöskupnö"),                 # the account is filed under a longer name
    ], aliases)
    meetings = _meetings([
        ("Bjölkärpe Energi AB", "Anna Varkström", False),
        ("Bjölkärpe Energi AB", "Per Ögvgren", True),
        ("Stiftelsen Hjylpödru", "Lena Gorvdahl", False),
        ("Mjöskupnö Skog AB", "Johan Nispman", False),
        ("Gnyvuxne Bygg AB", "Sara Mirrqvist", False),   # a namesake at another account
    ], aliases)
    won = _won([("Bjölkärpe Energi", "Initiative North", 120_000.0), ("Bjölkärpe Energi AB", "Inbound", 60_000.0)], aliases)
    return people, meetings, won, bucket(merge_repeats(people), meetings, won, LEFT_TAG)


def test_a_person_listed_twice_becomes_one_row():
    people, _, _, _ = _run()
    merged = merge_repeats(people)
    assert len(merged) == len(people) - 1
    assert (merged["person_key"] == "anna varkström").sum() == 1


def test_known_person_goes_to_personal_match_only():
    _, _, _, sheets = _run()
    assert list(sheets[PERSONAL]["Name"].map(person_key)) == ["anna varkström"]
    assert "anna varkström" not in set(sheets[ACCOUNT]["Name"].map(person_key))


def test_new_person_at_known_account_sees_who_was_met_before():
    _, _, _, sheets = _run()
    row = sheets[ACCOUNT].set_index("Name").loc["Erik Toskelius"]
    assert row["CRM_Account"] == "Bjölkärpe Energi AB"
    assert "Per Ögvgren (LEFT)" in row["Past_Contacts"] and "Anna Varkström" in row["Past_Contacts"]
    assert row["Prior_Contact_Status"] == "some contacts have left"


def test_paying_account_lists_everyone_there_with_the_total():
    _, _, _, sheets = _run()
    assert len(sheets[PAID]) == 2
    assert set(sheets[PAID]["Total_Won_SEK"]) == {180_000.0}
    assert sheets[PAID]["Lead_Sources"].iloc[0] == "Initiative North; Inbound"   # largest first


def test_a_namesake_at_another_account_is_not_a_match():
    _, _, _, sheets = _run()
    everyone = pd.concat(sheets.values())["Name"]
    assert "Sara Mirrqvist" not in set(everyone)


def test_nothing_is_matched_on_resemblance():
    """Mjöskupnö and Mjöskupnö Skog AB may well be one organisation. The program does not guess."""
    _, _, _, sheets = _run()
    assert "Karin Jäldlund" not in set(pd.concat(sheets.values())["Name"])


def test_an_alias_line_makes_the_match():
    _, _, _, sheets = _run(AliasTable(aliases={"Mjöskupnö Skog AB": ["Mjöskupnö"]}))
    assert "Karin Jäldlund" in set(sheets[ACCOUNT]["Name"])


def test_colliding_names_are_matched_until_a_person_keeps_them_apart():
    _, _, _, before = _run()
    assert "Nils Fenkberg" in set(before[ACCOUNT]["Name"])        # the wrong match the audit exists to show
    _, _, _, after = _run(AliasTable(keep_apart=["Stiftelsen Hjylpödru", "Hjylpödru AB"]))
    assert "Nils Fenkberg" not in set(pd.concat(after.values())["Name"])


def test_collisions_list_only_keys_with_different_spellings():
    aliases = AliasTable()
    people, meetings, won, _ = _run(aliases)
    collisions = {c.key: c for c in find_collisions(people, meetings, won, aliases)}
    assert set(collisions) == {"bjölkärpe energi", "hjylpödru"}
    assert collisions["hjylpödru"].programme == ["Hjylpödru AB"] and collisions["hjylpödru"].meetings == ["Stiftelsen Hjylpödru"]
    assert not collisions["hjylpödru"].decided
    # Case alone is not a different spelling.
    assert collisions["bjölkärpe energi"].spellings == ["Bjölkärpe Energi", "Bjölkärpe Energi AB"]


def test_a_collision_is_decided_once_the_table_covers_it():
    aliases = AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe Energi"]}, keep_apart=["Stiftelsen Hjylpödru", "Hjylpödru AB"])
    people, meetings, won, _ = _run(aliases)
    collisions = find_collisions(people, meetings, won, aliases)
    assert [c.key for c in collisions] == ["bjölkärpe energi"] and collisions[0].decided


def test_a_new_spelling_on_a_decided_key_goes_back_to_review():
    aliases = AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe Energi"]})
    people = _people([("Erik Toskelius", "Bjölkärpe Energi Sverige AB")], aliases)
    meetings = _meetings([("Bjölkärpe Energi AB", "Anna Varkström", False)], aliases)
    collisions = find_collisions(people, meetings, _won([], aliases), aliases)
    assert [c.key for c in collisions] == ["bjölkärpe energi"] and not collisions[0].decided


def test_a_lookalike_of_kept_apart_names_is_listed_even_alone():
    aliases = AliasTable(keep_apart=["Stiftelsen Hjylpödru", "Hjylpödru AB"])
    people = _people([("Nils Fenkberg", "Hjylpödru AB (publ)")], aliases)
    meetings = _meetings([("Stiftelsen Hjylpödru", "Lena Gorvdahl", False)], aliases)
    (collision,) = find_collisions(people, meetings, _won([], aliases), aliases)
    assert collision.kept_apart == ["Hjylpödru AB", "Stiftelsen Hjylpödru"] and not collision.decided
    assert collision.spellings == ["Hjylpödru AB (publ)"]


def test_a_known_person_marked_as_left_keeps_the_tag():
    aliases = AliasTable()
    people = _people([("Per Ögvgren", "Bjölkärpe Energi AB"), ("Anna Varkström", "Bjölkärpe Energi AB")], aliases)
    meetings = _meetings([("Bjölkärpe Energi AB", "Per Ögvgren", True), ("Bjölkärpe Energi AB", "Anna Varkström", False)], aliases)
    personal = bucket(merge_repeats(people), meetings, _won([], aliases), LEFT_TAG)[PERSONAL].set_index("Name")
    assert personal.loc["Per Ögvgren", "Marked_As_Left"] == "LEFT"
    assert personal.loc["Anna Varkström", "Marked_As_Left"] == ""
