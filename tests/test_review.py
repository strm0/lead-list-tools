"""The review screen's logic and its small server, on a generated world."""
import http.client
import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from leadlist import config, crossref, review, scrape_programme
from leadlist.mock import generate, server
from leadlist.normalize import AliasTable


@pytest.fixture()
def session(tmp_path):
    settings = config.Settings(root=tmp_path, work=tmp_path / "work", aliases=tmp_path / "config" / "org_aliases.yaml",
                               seed=7, events=120, port=0)
    generate.generate(settings)
    with server.running(settings.mock, settings.host, settings.port) as url:
        scrape_programme.run(url, settings)
    crossref.phase_one(settings)
    return review.ReviewSession(settings)


def _answers(session):
    return json.loads(session.settings.truth_file.read_text("utf-8"))["review"]


def test_explain_marks_what_the_normaliser_sets_aside():
    assert review.explain("Bjölkärpe Energi Sverige AB") == [
        ["Bjölkärpe", "kept"], ["Energi", "kept"], ["Sverige", "dropped"], ["AB", "dropped"]]
    assert review.explain("Stiftelsen Hjylpödru") == [["Stiftelsen", "dropped"], ["Hjylpödru", "kept"]]
    assert review.explain("Gnyvuxne och Tvöskömta") == [["Gnyvuxne", "kept"], ["och", "same"], ["Tvöskömta", "kept"]]
    assert review.explain("QÖ Sweden")[-1] == ["Sweden", "kept"]      # too little would be left


def test_it_will_not_open_before_phase_one(tmp_path):
    settings = config.Settings(root=tmp_path, work=tmp_path / "work", aliases=tmp_path / "aliases.yaml")
    with pytest.raises(SystemExit, match="Nothing to review"):
        review.ReviewSession(settings)


def test_same_organisation_writes_an_alias_and_clears_the_row(session):
    before = session.state()
    first = before["to_review"][0]
    session.same(first["spellings"], first["canonical"])
    after = session.state()
    assert len(after["to_review"]) == len(before["to_review"]) - 1
    assert first["key"] not in {c["key"] for c in after["to_review"]}
    assert first["canonical"] in AliasTable.load(session.settings.aliases).aliases


def test_keep_apart_separates_a_pair_that_normalises_alike(session):
    foundation, company = _answers(session)["keep_apart"][:2]
    pair = next(c for c in session.state()["to_review"] if {foundation, company} <= set(c["spellings"]))
    assert len(pair["forms"]) == 2
    session.apart(pair["forms"])
    table = AliasTable.load(session.settings.aliases)
    assert table.key(foundation).key != table.key(company).key
    assert pair["key"] not in {c["key"] for c in session.state()["to_review"]}


def test_linking_by_hand_joins_names_the_program_would_not(session):
    unmatched = {item["name"] for item in session.state()["unmatched"]["programme"]}
    known = AliasTable.load(session.settings.aliases).aliases
    canonical, variant = next(
        (name, v) for name, variants in _answers(session)["aliases"].items() if name not in known
        for v in variants if v in unmatched)
    session.link(variant, canonical)
    assert variant not in {item["name"] for item in session.state()["unmatched"]["programme"]}
    assert AliasTable.load(session.settings.aliases).key(variant).via == "alias"


def test_undo_and_take_back(session):
    start = session.settings.aliases.read_text("utf-8")
    first = session.state()["to_review"][0]
    session.same(first["spellings"], first["canonical"])
    assert session.state()["can_undo"]
    session.undo()
    assert session.settings.aliases.read_text("utf-8") == start and not session.state()["can_undo"]
    session.same(first["spellings"], first["canonical"])
    session.forget("same", first["canonical"])
    assert first["key"] in {c["key"] for c in session.state()["to_review"]}


def test_a_hand_edit_of_the_file_shows_up(session):
    first = session.state()["to_review"][0]
    table = AliasTable.load(session.settings.aliases)
    table.add_alias(first["canonical"], first["spellings"])
    table.save(session.settings.aliases)                # as if a person had edited the YAML
    assert first["key"] not in {c["key"] for c in session.state()["to_review"]}


def test_only_spellings_from_this_run_can_be_written(session):
    with pytest.raises(ValueError, match="not a spelling"):
        session.same(["Something Typed In"], "Something Else")


def test_undo_will_not_overwrite_a_hand_edit(session):
    first, second = session.state()["to_review"][:2]
    session.same(first["spellings"], first["canonical"])
    table = AliasTable.load(session.settings.aliases)
    table.add_alias(second["canonical"], second["spellings"])
    table.save(session.settings.aliases)                 # a person edits the file while the screen is open
    with pytest.raises(ValueError, match="edited by hand"):
        session.undo()
    assert second["canonical"] in AliasTable.load(session.settings.aliases).aliases


def test_taking_back_something_that_is_not_there_is_refused(session):
    with pytest.raises(ValueError, match="no such entry"):
        session.forget("same", "Never Listed AB")
    with pytest.raises(ValueError, match="no such entry"):
        session.forget("nonsense", "Never Listed AB")
    assert not session.state()["can_undo"]


def test_a_lookalike_of_kept_apart_names_is_resolved_by_saying_which_it_is(session):
    foundation, company = json.loads(session.settings.truth_file.read_text("utf-8"))["review"]["keep_apart"][:2]
    session.apart([[foundation], [company]])
    table = AliasTable.load(session.settings.aliases)
    assert table.kept_apart_lookalikes(company + " (publ)") == [foundation, company]
    session._known.add(company + " (publ)")              # as if the programme had spelled it that way
    session.apart([[foundation], [company + " (publ)", company]])
    table = AliasTable.load(session.settings.aliases)
    assert table.key(company + " (publ)").key == table.key(company).key != table.key(foundation).key
    assert table.keep_apart.count(company) == 1


def _call(url, path, token=None, host=None, body=b"{}"):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Review-Token"] = token
    if host:
        headers["Host"] = host
    request = urllib.request.Request(url + path, data=body if path.startswith("api/") and path != "api/state" else None,
                                     headers=headers)
    try:
        with urllib.request.urlopen(request) as answer:
            return answer.status, answer.read().decode("utf-8")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode("utf-8")


def test_the_server_over_http(session):
    httpd, url = review.make_server(session.settings)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, page = _call(url, "")
        assert status == 200 and "Possible collisions" in page and "leadlist phase2" in page
        token = re.search(r'const TOKEN = "([^"]+)"', page).group(1)
        status, body = _call(url, "api/state")
        state = json.loads(body)
        assert status == 200 and state["to_review"] and not state["phase_two_ran"]

        assert _call(url, "api/undo")[0] == 403                              # no token
        assert _call(url, "api/undo", token="wrong")[0] == 403
        assert _call(url, "", host="evil.example")[0] == 403                 # another site's name for this machine
        assert _call(url, "api/undo", token=token, host="evil.example")[0] == 403
        assert _call(url, "api/nothing", token=token)[0] == 404
        assert _call(url, "api/same", token=token, body=b"not json")[0] == 400

        first = state["to_review"][0]
        decision = json.dumps({"spellings": first["spellings"], "canonical": first["canonical"]}).encode()
        status, body = _call(url, "api/same", token=token, body=decision)
        assert status == 200 and first["key"] not in {c["key"] for c in json.loads(body)["to_review"]}

        session.settings.aliases.write_text("aliases: [oops", encoding="utf-8")   # a slip while editing by hand
        status, body = _call(url, "api/state")
        assert status == 500 and "not valid YAML" in json.loads(body)["error"]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _raw(url, method, path, headers=None, body=b""):
    """One request with exactly these headers, whatever they claim. Returns status, headers and body."""
    connection = http.client.HTTPConnection(url.removeprefix("http://").rstrip("/"), timeout=10)
    try:
        connection.putrequest(method, path, skip_host=True)
        for name, value in ({"Host": connection.host + f":{connection.port}"} | (headers or {})).items():
            connection.putheader(name, value)
        connection.endheaders()
        connection.send(body)
        answer = connection.getresponse()
        return answer.status, {name.lower(): value for name, value in answer.getheaders()}, answer.read().decode("utf-8")
    finally:
        connection.close()


def test_every_answer_refuses_to_be_framed_or_sniffed(session):
    httpd, url = review.make_server(session.settings)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, headers, page = _raw(url, "GET", "/")
        token = re.search(r'const TOKEN = "([^"]+)"', page).group(1)
        assert status == 200 and headers["content-security-policy"] == review.PAGE_POLICY
        assert headers["content-security-policy"] == (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

        posted = {"X-Review-Token": token, "Content-Length": "2"}
        answers = {
            "the page": (200, _raw(url, "GET", "/")),
            "the state": (200, _raw(url, "GET", "/api/state")),
            "the icon": (204, _raw(url, "GET", "/favicon.ico")),
            "not found": (404, _raw(url, "GET", "/nothing")),
            "another host": (403, _raw(url, "GET", "/", {"Host": "evil.example"})),
            "no token": (403, _raw(url, "POST", "/api/undo", {"Content-Length": "2"}, b"{}")),
            "a decision": (200, _raw(url, "POST", "/api/undo", posted, b"{}")),
            "a bad decision": (400, _raw(url, "POST", "/api/same", posted, b"{}")),
            "a method the screen does not have": (501, _raw(url, "DELETE", "/api/state")),   # written by the base class
        }
        for what, (expected, (status, headers, _)) in answers.items():
            assert status == expected, what
            assert headers.get("x-frame-options") == "DENY", what
            assert "frame-ancestors 'none'" in headers.get("content-security-policy", ""), what
            assert headers.get("x-content-type-options") == "nosniff", what
            if what != "the page":
                assert "unsafe-inline" not in headers["content-security-policy"], what
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_request_body_is_measured_before_it_is_read(session):
    httpd, url = review.make_server(session.settings)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        token = re.search(r'const TOKEN = "([^"]+)"', _raw(url, "GET", "/")[2]).group(1)
        before = session.settings.aliases.read_text("utf-8")

        def post(length, body=b"", path="/api/undo"):
            headers = {"X-Review-Token": token} | ({} if length is None else {"Content-Length": length})
            status, _, answer = _raw(url, "POST", path, headers, body)
            return status, json.loads(answer).get("error", "")

        # No body is sent with these: an answer at all means the server did not wait to read one.
        for length in ("-1", "-9999999999", "many", "1.5", "0x10", "1e3", "", "+5", b"\xb2", "5, 5"):       # b2: a raised 2, a digit that is no number
            assert post(length) == (400, "Content-Length must be a whole number, zero or more"), length
        for length in (str(review.MAX_BODY_BYTES + 1), "99999999999", "9" * 5000):
            assert post(length) == (413, "the request is too large"), length
        assert review.MAX_BODY_BYTES == 1024 * 1024

        assert post(None)[0] == 200 and post("0")[0] == 200            # nothing to undo, and nothing wrong with asking
        deep = b"[" * 200_000 + b"]" * 200_000                            # JSON nested deeper than Python will follow
        assert post(str(len(deep)), deep, "/api/same") == (400, "the request is nested too deeply")
        for body in (b"[]", b'"text"', b"7", b"null", b'{"spellings": 5, "canonical": {}}', b"\xff\xfe"):
            assert post(str(len(body)), body, "/api/same")[0] == 400, body

        assert _raw(url, "GET", "/api/state")[0] == 200                  # the screen is still up after all of that
        assert session.settings.aliases.read_text("utf-8") == before
    finally:
        httpd.shutdown()
        httpd.server_close()
