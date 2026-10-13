"""The whole pipeline on a small generated world, checked against its answer key."""
import json

import pytest

from leadlist import cli, config, crossref, municipality_filter, report, report_page, scrape_congress, scrape_programme
from leadlist.mock import generate, reviewer, server
from leadlist.normalize import AliasTable
from leadlist.workbook import read_sheet, write_workbook


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    root = tmp_path_factory.mktemp("project")
    return config.Settings(root=root, work=root / "work", aliases=root / "config" / "org_aliases.yaml",
                           seed=7, events=80, port=0)


@pytest.fixture(scope="module")
def scraped(settings):
    generate.generate(settings)
    with server.running(settings.mock, settings.host, settings.port) as url:
        programme = scrape_programme.run(url, settings)
        congress = scrape_congress.run(url, settings)
    return programme, congress


def _lists(settings):
    return report.build(settings)["lists"].values()


def test_generation_is_repeatable(settings, scraped, tmp_path):
    other = settings.with_changes(root=tmp_path, work=tmp_path / "work", aliases=tmp_path / "aliases.yaml")
    generate.generate(other)
    assert other.truth_file.read_text("utf-8") == settings.truth_file.read_text("utf-8")
    different = other.with_changes(seed=8)
    generate.generate(different)
    assert different.truth_file.read_text("utf-8") != settings.truth_file.read_text("utf-8")


def test_scraper_reads_every_event_and_person(settings, scraped):
    programme, _ = scraped
    key = json.loads(settings.truth_file.read_text("utf-8"))
    assert programme["events"] == 80
    scraped_names = set(read_sheet(settings.output / "participants.xlsx", "All_Participants")["Name"].str.lower())
    expected = {" ".join(key["people"][p]["name"].lower().split()) for p in key["at_event"]}
    assert {" ".join(name.split()) for name in scraped_names} == expected


def test_congress_scraper_pages_through_the_api(settings, scraped):
    _, congress = scraped
    assert congress["sessions"] == 70 and congress["sponsors"] == 40 and congress["tiers"] == 4
    speakers = read_sheet(settings.output / "congress.xlsx", "Speakers")
    assert not speakers["Person"].map(scrape_congress.is_placeholder).any()
    assert all(scrape_congress.is_placeholder(slot) for slot in ("TBA", "Faculty", "All speakers", "the panel", ""))
    assert not scrape_congress.is_placeholder("Edit Snarpvall")
    assert (speakers["Employed_By_Sponsor"] != "").any()
    assert (read_sheet(settings.output / "congress.xlsx", "Sponsors")["Known_Account"] == "Yes").sum() == 8


def test_filter_agrees_with_the_answer_key(settings, scraped):
    municipality_filter.run(settings)
    crossref.phase_one(settings)
    crossref.phase_two(settings, accept_undecided=True)
    flt = report.build(settings)["filter"]
    assert flt["wrong"] == 0 and flt["matched"] > 0 and flt["parse_issues"] > 0


def test_phase_two_refuses_to_run_before_phase_one(settings, scraped):
    (settings.state / "phase1.json").unlink(missing_ok=True)
    with pytest.raises(SystemExit, match="Phase one has not run"):
        crossref.phase_two(settings, accept_undecided=True)


def test_phase_one_stops_and_clears_what_followed_from_an_earlier_one(settings, scraped, capsys):
    crossref.phase_one(settings)
    crossref.phase_two(settings, accept_undecided=True)
    report_page.write(settings, report.build(settings))
    assert (settings.output / "lead_lists.xlsx").exists()
    crossref.phase_one(settings)
    assert "STOP" in capsys.readouterr().out
    assert (settings.output / "phase1_audit.xlsx").exists()
    for stale in ("lead_lists.xlsx", "match_audit.xlsx", "run_report.html", "run_report.json"):
        assert not (settings.output / stale).exists()
    with pytest.raises(SystemExit, match="Nothing to report"):
        report.build(settings)


def test_phase_two_refuses_when_the_inputs_changed_after_phase_one(settings, scraped):
    crossref.phase_one(settings)
    participants = settings.output / "participants.xlsx"
    original = participants.read_bytes()
    sheets = {name: read_sheet(participants, name) for name in ("All_Participants", "Occurrences", "Events")}
    sheets["All_Participants"].loc[len(sheets["All_Participants"])] = ["Nils Fenkberg", "VD", "", "Dvälpyrgu Data AB", "Participant", "1"]
    write_workbook(participants, sheets)
    try:
        with pytest.raises(SystemExit, match="changed since phase one"):
            crossref.phase_two(settings, accept_undecided=True)
    finally:
        participants.write_bytes(original)
    crossref.phase_two(settings, accept_undecided=True)


def test_the_statement_says_so_when_the_review_was_not_complete(settings, scraped):
    crossref.phase_one(settings)
    crossref.phase_two(settings, accept_undecided=True)
    unreviewed = report.build(settings)                      # nothing reviewed: wrong rows and missed people
    page = report_page.render({**unreviewed, "before_review": unreviewed})
    assert "Rows the answer key expects" in page and "wrong rows still on the lists" in page
    for score in unreviewed["lists"].values():               # every row is counted once, right or wrong
        assert score["rows"] == score["right"] + score["wrong"]
    assert any("another organisation's history" in row for s in unreviewed["lists"].values() for row in s["wrong_rows"])


def test_the_demo_runs_to_the_end_and_balances(tmp_path, capsys):
    config_file = tmp_path / "config" / "settings.toml"
    config_file.parent.mkdir()
    config_file.write_text('[mock]\nseed = 3\nevents = 60\nport = 0\n', encoding="utf-8")
    cli.main(["--config", str(config_file), "demo"])
    out = capsys.readouterr().out
    assert "STOP" in out and "Stand-in reviewer" in out
    page = (tmp_path / "work" / "output" / "run_report.html").read_text("utf-8")
    assert "Rows after the review" in page and "Rows the answer key expects" not in page
    assert "Unaccounted for" not in page
    latest = json.loads((tmp_path / "work" / "output" / "run_report.json").read_text("utf-8"))
    assert all(s["wrong"] == 0 and s["missed"] == 0 and s["rows"] > 0 for s in latest["lists"].values())
    assert latest["review"]["left_undecided"] == 0


def test_unreviewed_run_makes_mistakes_the_review_removes(settings, scraped):
    crossref.phase_one(settings)
    crossref.phase_two(settings, accept_undecided=True)
    before = list(_lists(settings))
    assert sum(score["wrong"] for score in before) > 0      # names that collide and should not
    assert sum(score["missed"] for score in before) > 0     # spellings only a person can join

    # A careful reviewer: every alias line and keep-apart name the answer key holds.
    review = json.loads(settings.truth_file.read_text("utf-8"))["review"]
    table = AliasTable.load(settings.aliases)
    for canonical, variants in review["aliases"].items():
        table.add_alias(canonical, variants)
    table.add_keep_apart(review["keep_apart"])
    table.save(settings.aliases)

    crossref.phase_two(settings, accept_undecided=True)                            # phase two reads the table as it stands now
    after = list(_lists(settings))
    assert [score["wrong"] for score in after] == [0, 0, 0]
    assert [score["missed"] for score in after] == [0, 0, 0]
    assert all(score["rows"] == score["right"] > 0 for score in after)


def test_phase_two_refuses_while_a_collision_is_undecided(tmp_path):
    settings = config.Settings(root=tmp_path, work=tmp_path / "work", aliases=tmp_path / "config" / "a.yaml",
                               seed=5, events=60, port=0)
    generate.generate(settings)
    with server.running(settings.mock, settings.host, settings.port) as url:
        scrape_programme.run(url, settings)
    crossref.phase_one(settings)
    with pytest.raises(SystemExit, match="have no decision yet"):
        crossref.phase_two(settings)
    assert not (settings.output / "lead_lists.xlsx").exists()

    # A review by hand, with no demo involved: the report still compares before and after.
    reviewer.review_from_answer_key(settings)
    crossref.phase_two(settings)
    latest = report.build(settings)
    assert latest["review"]["left_undecided"] == 0
    assert latest["before_review"] and (settings.output / "run_report_unreviewed.json").exists()
    assert sum(s["wrong"] + s["missed"] for s in latest["before_review"]["lists"].values()) > 0
    assert all(s["wrong"] == 0 and s["missed"] == 0 for s in latest["lists"].values())
    page = report_page.render(latest)
    assert "Rows after the review" in page and "municipality filter was not run" in page


def test_no_municipality_is_ever_on_the_crm_side(settings, scraped):
    key = json.loads(settings.truth_file.read_text("utf-8"))
    municipal = {oid for oid, org in key["organisations"].items() if org["kind"] == "municipality"}
    assert municipal and not municipal & (set(key["meeting_accounts"]) | set(key["won_accounts"]))
    table = AliasTable.load(settings.aliases)
    listed = set(table.aliases) | {v for variants in table.aliases.values() for v in variants} | set(table.keep_apart)
    listed |= set(key["review"]["aliases"]) | {v for vs in key["review"]["aliases"].values() for v in vs}
    assert not {name for name in listed if key["organisations"].get(key["spellings"].get(name), {}).get("kind") == "municipality"}
    at_event = {key["people"][p]["organisation"] for p in key["at_event"]}
    assert municipal & at_event                      # they are still in the programme, where the filter needs them


def test_a_settings_file_cannot_aim_the_work_folder_outside_the_project(tmp_path):
    config_file = tmp_path / "config" / "settings.toml"
    config_file.parent.mkdir()
    config_file.write_text('[paths]\nwork = "../elsewhere"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="must lie inside"):
        config.load(config_file)
    config_file.write_text('[paths]\naliases = "/tmp/anywhere.yaml"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="must lie inside"):
        config.load(config_file)
