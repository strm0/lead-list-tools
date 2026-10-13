"""The municipality rules. Municipality names are the official ones; all else is invented."""
import pandas as pd
import pytest

from leadlist.municipalities import (
    MATCHED, PARSE_ISSUE, REJECTED, SELF_TEST_CASES, classify, official_names, self_test,
)
from leadlist.municipality_filter import sort_people


def test_the_list_has_all_290():
    assert len(set(official_names().values())) == 290


@pytest.mark.parametrize("organisation, status, municipality", SELF_TEST_CASES)
def test_known_cases(organisation, status, municipality):
    verdict = classify(organisation)
    assert (verdict.status, verdict.municipality) == (status, municipality)


def test_self_test_passes(capsys):
    assert self_test()
    assert "FAIL" not in capsys.readouterr().out


@pytest.mark.parametrize("stad, kommun", [("Malmö stad", "Malmö kommun"), ("Solna Stad", "Solna kommun")])
def test_stad_and_kommun_are_the_same_municipality(stad, kommun):
    assert classify(stad).municipality == classify(kommun).municipality != ""


def test_genitive_s_is_not_taken_off_a_name_that_ends_in_s():
    assert classify("Grums kommun").municipality == "Grums"
    assert classify("Hofors kommun").municipality == "Hofors"


def test_a_place_in_the_name_is_not_enough():
    assert classify("Umeå Vryskarkiv").status == REJECTED
    assert classify("Lund").status == REJECTED


def test_gotland_is_the_only_region_that_counts():
    assert classify("Region Gotland").status == MATCHED
    assert classify("Hållbarhetsenheten, Region Gotland").municipality == "Gotland"
    assert classify("Region Knävdirpsa").status == REJECTED


def test_every_person_lands_in_exactly_one_sheet():
    people = pd.DataFrame([
        {"Name": "Anna Varkström", "Title": "Strateg", "Email": "", "Organisation": "Lunds kommun", "Event_IDs": "1001"},
        {"Name": "anna varkström", "Title": "Strateg och utredare", "Email": "anna@lund.example",
         "Organisation": "Kulturförvaltningen, Lunds kommun", "Event_IDs": "1007"},
        {"Name": "Erik Toskelius", "Title": "VD", "Email": "", "Organisation": "Bjölkärpe Energi AB", "Event_IDs": "1002"},
        {"Name": "Sara Mirrqvist", "Title": "Utredare", "Email": "", "Organisation": "Knävdirpsa kommun", "Event_IDs": "1003"},
    ])
    sheets = sort_people(people)
    assert len(sheets["Matched"]) == 1 and len(sheets["Rejected"]) == 1 and len(sheets["Parse_Issues"]) == 1
    merged = sheets["Matched"].iloc[0]
    assert merged["Municipality"] == "Lund"
    assert merged["Title"] == "Strateg och utredare"       # the fullest title
    assert merged["Email"] == "anna@lund.example"          # the first address given
    assert merged["Event_IDs"] == "1001, 1007"
    assert sheets["Parse_Issues"].iloc[0]["Reason"].startswith("says kommun")
    assert classify("Knävdirpsa kommun").status == PARSE_ISSUE


def test_letters_typed_as_two_characters_are_read_the_same():
    assert classify("Malmo\u0308 stad").municipality == "Malmö"


def test_doubt_goes_to_a_person_not_to_a_guess():
    for doubtful in ("Lunds kommun och Malmö stad", "Vara Stad Kvärpågmy AB", "Region Spjurkupnö och Lunds kommun"):
        assert classify(doubtful).status == PARSE_ISSUE, doubtful
    assert classify("Region Gotlands krymtmuseum AB").status == REJECTED
