"""The normaliser and the alias table. Every organisation named here is invented."""
import pytest

from leadlist.normalize import AliasTable, base_key, clean, display_name, person_key, split_left_tag, tidy


@pytest.mark.parametrize("spelling", [
    "Bjölkärpe Energi AB",
    "bjölkärpe energi",
    "BJÖLKÄRPE ENERGI AB",
    "  Bjölkärpe   Energi  Ab. ",
    "Bjölkärpe Energi Sverige AB",
    "Bjölkärpe Energi AB (publ)",
    "Bjölkärpe Energi Aktiebolag",
])
def test_spellings_of_one_company_share_a_key(spelling):
    assert base_key(spelling) == "bjölkärpe energi"


def test_ampersand_is_written_as_och():
    assert base_key("Gnyvuxne & Tvöskömta Arkitekter") == base_key("Gnyvuxne och Tvöskömta Arkitekter AB")
    assert base_key("Gnyvuxne&Tvöskömta Arkitekter") == "gnyvuxne och tvöskömta arkitekter"


def test_punctuation_is_removed_not_replaced_by_a_space():
    assert clean("Q.Y.J. Data") == "qyj data"


def test_affixes_come_off_both_ends():
    assert base_key("Stiftelsen Mjöskupnö") == "mjöskupnö"
    assert base_key("The Mjöskupnö Group") == "mjöskupnö"


def test_an_affix_stays_when_too_little_would_be_left():
    assert base_key("QÖ Sweden") == "qö sweden"
    assert base_key("AB") == "ab"


def test_different_names_stay_different():
    assert base_key("Bjölkärpe Energi AB") != base_key("Bjölkärpe Bygg AB")
    assert base_key("Bjölkärpe") != base_key("Bjölkärpe Energi AB")


def test_empty_values():
    assert base_key(None) == "" and base_key("  ") == "" and person_key(float("nan")) == ""


def test_people_match_on_case_and_spacing_only():
    assert person_key("  Anna   VARKSTRÖM ") == "anna varkström"
    assert person_key("Anna Varkström") != person_key("Anna Varkstrom")


@pytest.mark.parametrize("contact, name, left", [
    ("Erik Toskelius (LEFT)", "Erik Toskelius", True),
    ("Erik Toskelius (left 2024)", "Erik Toskelius", True),
    ("Erik Toskelius ( LEFT, retired )", "Erik Toskelius", True),
    ("Erik Toskelius (LEFTOVER)", "Erik Toskelius (LEFTOVER)", False),
    ("Erik Toskelius", "Erik Toskelius", False),
    ("Erik (Leftwich) Toskelius", "Erik (Leftwich) Toskelius", False),
    (None, "", False),
])
def test_left_tag(contact, name, left):
    assert split_left_tag(contact, "LEFT") == (name, left)


def test_alias_joins_spellings_the_normaliser_cannot():
    table = AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe", "Bjölkärpe Kraft"]})
    assert table.key("BJÖLKÄRPE").key == table.key("Bjölkärpe Energi").key == "bjölkärpe energi"
    assert table.key("Bjölkärpe").via == "alias"
    assert table.key("Bjölkärpe Energi AB").via == "direct"
    assert table.key("Bjölkärpe Bygg").key == "bjölkärpe bygg"


def test_alias_is_symmetric():
    """The canonical name goes through the same normaliser as its variants."""
    table = AliasTable(aliases={"BJÖLKÄRPE ENERGI AB (publ)": ["bjölkärpe"]})
    assert table.key("Bjölkärpe").key == table.key("Bjölkärpe Energi").key


def test_keep_apart_gives_each_name_its_own_key():
    assert base_key("Stiftelsen Hjylpödru") == base_key("Hjylpödru AB")
    table = AliasTable(keep_apart=["Stiftelsen Hjylpödru", "Hjylpödru AB"])
    foundation, company = table.key("STIFTELSEN HJYLPÖDRU"), table.key("hjylpödru ab")
    assert foundation.key != company.key
    assert foundation.via == company.via == "kept apart"


def test_a_variant_of_a_kept_apart_name_does_not_pull_its_neighbour_in():
    table = AliasTable(aliases={"Hjylpödru AB": ["Hjylpödru Aktiebolag"]}, keep_apart=["Stiftelsen Hjylpödru", "Hjylpödru AB"])
    assert table.key("Hjylpödru Aktiebolag").key == table.key("Hjylpödru AB").key
    assert table.key("Stiftelsen Hjylpödru").key != table.key("Hjylpödru AB").key


def test_table_survives_a_round_trip_to_disk(tmp_path):
    table = AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe"]})
    table.add_alias("Gnyvuxne & Tvöskömta Arkitekter", ["Gnyvuxne Tvöskömta"])
    table.add_keep_apart(["Stiftelsen Hjylpödru", "Hjylpödru AB"])
    path = tmp_path / "aliases.yaml"
    table.save(path)
    loaded = AliasTable.load(path)
    assert loaded.aliases == table.aliases and loaded.keep_apart == table.keep_apart
    assert loaded.entry_count() == 4
    assert loaded.knows("gnyvuxne tvöskömta") and not loaded.knows("Dvälpyrgu Data")


def test_a_missing_file_is_an_empty_table(tmp_path):
    assert AliasTable.load(tmp_path / "nothing.yaml").entry_count() == 0


def test_display_name_prefers_ordinary_case_then_the_most_common():
    assert display_name(["ANNA VARKSTRÖM", "Anna Varkström"]) == "Anna Varkström"
    assert display_name(["Anna Varkstrom", "Anna Varkström", "Anna Varkström"]) == "Anna Varkström"
    assert display_name(["ANNA VARKSTRÖM"]) == "ANNA VARKSTRÖM"
    assert display_name([]) == ""


def test_a_decision_covers_only_the_spellings_it_names():
    table = AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe"]})
    assert table.knows("BJÖLKÄRPE ENERGI AB") and table.knows("bjölkärpe.")
    assert not table.knows("Stiftelsen Bjölkärpe Energi")       # normalises alike, but nobody ruled on it
    assert not table.knows("Bjölkärpe Energi Sverige AB")


def test_chained_entries_mean_the_same_in_any_order_and_after_saving(tmp_path):
    one = AliasTable(aliases={"Bjölkärpe Kraft AB": ["Bjölkärpe"], "Bjölkärpe Energi AB": ["Bjölkärpe Kraft AB"]})
    other = AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe Kraft AB"], "Bjölkärpe Kraft AB": ["Bjölkärpe"]})
    one.save(tmp_path / "a.yaml")
    for table in (one, other, AliasTable.load(tmp_path / "a.yaml")):
        assert table.key("Bjölkärpe").key == table.key("Bjölkärpe Kraft").key == table.key("Bjölkärpe Energi AB").key == "bjölkärpe energi"


def test_a_spelling_cannot_be_listed_under_two_names(tmp_path):
    table = AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe"]})
    with pytest.raises(ValueError, match="listed under both"):
        table.add_alias("Bjölkärpe Bygg AB", ["Bjölkärpe"])
    assert "Bjölkärpe Bygg AB" not in table.aliases and table.key("Bjölkärpe").key == "bjölkärpe energi"
    path = tmp_path / "bad.yaml"
    path.write_text("aliases:\n  Bjölkärpe Energi AB: [Bjölkärpe]\n  Bjölkärpe Bygg AB: [Bjölkärpe]\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="contradicts itself"):
        AliasTable.load(path)


def test_entries_listed_under_each_other_are_refused():
    with pytest.raises(ValueError, match="circle"):
        AliasTable(aliases={"Bjölkärpe Energi AB": ["Bjölkärpe Kraft AB"]}).add_alias("Bjölkärpe Kraft AB", ["Bjölkärpe Energi AB"])


def test_a_spelling_that_looks_like_kept_apart_names_is_pointed_out():
    table = AliasTable(keep_apart=["Stiftelsen Hjylpödru", "Hjylpödru AB"])
    assert table.kept_apart_lookalikes("Hjylpödru AB (publ)") == ["Stiftelsen Hjylpödru", "Hjylpödru AB"]
    assert table.kept_apart_lookalikes("hjylpödru ab") == [] and table.kept_apart_lookalikes("Bjölkärpe AB") == []
    table.add_alias("Hjylpödru AB", ["Hjylpödru AB (publ)"])
    assert table.kept_apart_lookalikes("Hjylpödru AB (publ)") == []
    assert table.key("Hjylpödru AB (publ)").key == table.key("Hjylpödru AB").key


def test_broken_yaml_is_reported_plainly(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("aliases: [oops", encoding="utf-8")
    with pytest.raises(SystemExit, match="not valid YAML"):
        AliasTable.load(path)


def test_the_same_letters_typed_two_ways_are_one_spelling():
    assert clean("Snörvulpte Energi") == clean("Snörvulpte Energi")   # o + combining dots, and ö
    assert person_key("Björn Varkström") == person_key("Björn Varkström")


def test_characters_nobody_can_see_do_not_make_a_new_spelling():
    assert clean("Krymt​ivnö Bygg") == clean("Krymtivnö Bygg")          # zero-width space
    assert clean("Krymt­ivnö Bygg") == clean("Krymtivnö Bygg")          # soft hyphen
    assert clean("‮Krymtivnö Bygg") == clean("Krymtivnö Bygg")          # direction mark
    assert person_key("Edit\tSnarpvall\x00") == "edit snarpvall"             # control characters are spaces
    assert tidy("Edit \ud800Snarpvall") == "Edit Snarpvall"                  # a lone surrogate cannot be saved


def test_a_name_that_reads_nan_is_a_name():
    assert clean("Nan") == "nan" and person_key("NaN") == "nan"
    assert clean(float("nan")) == "" and split_left_tag(float("nan"), "LEFT") == ("", False)
