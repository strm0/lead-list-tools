"""The scrapers against a site that means harm.

Each test starts a small server on this machine that answers the way a hostile
programme site or congress API might, and checks that the scraper stays on the
site it was pointed at, stops in time and says plainly what went wrong.
"""
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest

from leadlist import cli, config, fetch, scrape_congress, scrape_programme, workbook
from leadlist.fetch import Fetcher
from leadlist.workbook import read_sheet, write_workbook


@contextmanager
def site(answer):
    """Serve with answer(path, query) -> (status, headers, body) or None for a 404. Yields (url, paths asked for)."""
    asked: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            asked.append(self.path)
            parts = urlsplit(self.path)
            status, headers, body = answer(parts.path, parse_qs(parts.query)) or (404, {}, b"not here")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            if "Content-Length" not in headers and "no-length" not in headers:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()   # a short poll: quick to stop
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", asked
    finally:
        server.shutdown()
        server.server_close()


def page(body: str) -> tuple[int, dict, bytes]:
    return 200, {"Content-Type": "text/html; charset=utf-8"}, f"<html><body>{body}</body></html>".encode("utf-8")


def as_json(payload: object, headers: dict | None = None) -> tuple[int, dict, bytes]:
    return 200, {"Content-Type": "application/json", **(headers or {})}, json.dumps(payload).encode("utf-8")


def event_page(event_id: str, name: str, organisation: str) -> str:
    return (f'<article class="event" data-event-id="{event_id}"><h1>Frukostseminarium</h1>'
            f'<section class="participants"><ul><li class="person"><span class="name">{name}</span>'
            f'<span class="org">{organisation}</span></li></ul></section></article>')


@pytest.fixture()
def settings(tmp_path):
    # One try: a refusal should be immediate, with no waiting between attempts.
    return config.Settings(root=tmp_path, work=tmp_path / "work", aliases=tmp_path / "config" / "a.yaml",
                           port=0, retries=1, timeout_seconds=5)


def inside(folder) -> bool:
    """Every file under the work folder really is under it, and nothing was written beside it."""
    return all(path.resolve().is_relative_to(folder.resolve()) for path in folder.rglob("*"))


# -- 1. links that leave the site -----------------------------------------------------------------

def test_links_to_another_site_are_counted_and_not_followed(settings, capsys):
    with site(lambda path, query: page(event_page("9", "Edit Snarpvall", "Skvurmuxne Bygg AB"))) as (elsewhere, elsewhere_asked):
        here = {}                                       # this server's own address, known once it has started

        def answer(path, query):
            if path == "/programme/index.html":
                host = here["url"].removeprefix("http://")
                links = ["event-1.html", f"{elsewhere}/programme/event-9.html", "//other.example/x",
                         "javascript:alert(1)", "file:///etc/passwd", "mailto:edit@skvurmuxne.example",
                         "ftp://files.example/event.html", f"https://{host}/programme/event-2.html",
                         f"http://{host}@other.example/event.html", "http://[broken/event.html",
                         "event-1.html#again", "  "]
                return page("".join(f'<a class="event-link" href="{link}">x</a>' for link in links)
                            + f'<a rel="next" href="{elsewhere}/programme/page-2.html">next</a>')
            if path == "/programme/event-1.html":
                return page(event_page("1", "Edit Snarpvall", "Skvurmuxne Bygg AB"))

        with site(answer) as (url, asked):
            here["url"] = url
            counts = scrape_programme.run(url, settings)

    assert counts == {"events": 1, "person mentions": 1, "people": 1}
    assert asked == ["/programme/index.html", "/programme/event-1.html"]
    assert elsewhere_asked == []
    # Nine event links and the "next" link led away.
    assert f"10 links led away from {url} and were not followed" in capsys.readouterr().out


def test_the_fetcher_itself_refuses_another_site(settings):
    with site(lambda path, query: page("hello")) as (url, asked), site(lambda path, query: page("x")) as (other, other_asked):
        fetcher = Fetcher(url, settings.cache / "x", retries=1)
        assert "hello" in fetcher.text(f"{url}/a.html", "a.html")
        for address in (f"{other}/a.html", url.replace("http://", "https://") + "/a.html", "file:///etc/passwd",
                        "javascript:alert(1)", "//other.example/a", "other.example/a", f"{url}@other.example/a"):
            with pytest.raises(SystemExit, match="Refused to fetch|Could not fetch"):
                fetcher.text(address, "a.html")
        with pytest.raises(SystemExit, match="Refused to fetch"):
            fetcher.json(f"{other}/api", {"page": 1}, "a.json")
    assert other_asked == [] and asked == ["/a.html"]


# -- 2. redirects ----------------------------------------------------------------------------------

def test_a_redirect_to_another_site_is_not_followed(settings):
    with site(lambda path, query: page("stolen")) as (other, other_asked):
        def answer(path, query):
            if path == "/programme/index.html":
                return 302, {"Location": f"{other}/programme/index.html"}, b""
            if path == "/api/v1/days":
                return 307, {"Location": f"//{other.removeprefix('http://')}/api/v1/days"}, b""

        with site(answer) as (url, _):
            with pytest.raises(SystemExit, match="redirects to another site") as refused:
                scrape_programme.run(url, settings)
            assert other in str(refused.value)
            with pytest.raises(SystemExit, match="redirects to another site"):
                scrape_congress.run(url, settings)
    assert other_asked == []
    assert not list((settings.cache / "programme").iterdir())      # nothing was kept from the refused answer


def test_a_redirect_within_the_site_is_followed_and_a_circle_is_not(settings):
    def answer(path, query):
        if path == "/programme/index.html":
            return 301, {"Location": "/programme/moved/index.html"}, b""
        if path == "/programme/moved/index.html":
            return 302, {"Location": "list.html?lang=sv"}, b""
        if path == "/programme/moved/list.html" and query == {"lang": ["sv"]}:
            return page('<a class="event-link" href="/programme/event-1.html">x</a>'
                        '<a class="event-link" href="/programme/circle.html">y</a>')
        if path == "/programme/event-1.html":
            return page(event_page("1", "Edit Snarpvall", "Skvurmuxne Bygg AB"))
        if path == "/programme/circle.html":
            return 302, {"Location": "/programme/circle.html"}, b""

    with site(answer) as (url, asked):
        with pytest.raises(SystemExit, match=f"redirected more than {fetch.MAX_REDIRECTS} times"):
            scrape_programme.run(url, settings)
    assert asked[:4] == ["/programme/index.html", "/programme/moved/index.html", "/programme/moved/list.html?lang=sv",
                         "/programme/event-1.html"]
    assert asked.count("/programme/circle.html") == fetch.MAX_REDIRECTS + 1


# -- 3. size and time ------------------------------------------------------------------------------

def test_a_body_past_the_limit_is_refused_whether_or_not_its_length_is_stated(settings, monkeypatch):
    assert fetch.MAX_BODY_BYTES == 5 * 1024 * 1024
    big = b"<p>" + b"x" * fetch.MAX_BODY_BYTES + b"</p>"

    def answer(path, query):
        if path == "/programme/index.html":
            return 200, {}, big                                     # Content-Length says so up front
        if path == "/api/v1/days":
            return 200, {"no-length": "1"}, big                     # no length given: found out while reading
        if path == "/small.html":
            return 200, {"Content-Length": "10"}, b"0123456789"

    with site(answer) as (url, _):
        with pytest.raises(SystemExit, match="sent more than 5 MB"):
            scrape_programme.run(url, settings)
        with pytest.raises(SystemExit, match="sent more than 5 MB"):
            scrape_congress.run(url, settings)
        monkeypatch.setattr(fetch, "MAX_BODY_BYTES", 9)
        with pytest.raises(SystemExit, match="sent more than"):
            Fetcher(url, settings.cache / "x", retries=1).text(f"{url}/small.html", "small.html")
    assert not [path for path in settings.cache.rglob("*") if path.is_file()]


def test_every_request_carries_the_timeout_and_follows_no_redirect_on_its_own(settings, monkeypatch):
    with site(lambda path, query: as_json([])) as (url, _):
        fetcher = Fetcher(url, settings.cache / "x", timeout=7.5, retries=1)
        seen = []
        real = fetcher.session.get
        monkeypatch.setattr(fetcher.session, "get", lambda address, **how: seen.append(how) or real(address, **how))
        fetcher.json(f"{url}/api", {"page": 1}, "a.json")
        fetcher.text(f"{url}/page", "page")
    assert seen == [{"timeout": 7.5, "allow_redirects": False, "stream": True}] * 2


def test_a_server_that_stalls_is_given_up_on(settings):
    def answer(path, query):
        time.sleep(1.5)
        return page("late")

    with site(answer) as (url, _):
        started = time.monotonic()
        with pytest.raises(SystemExit, match="Gave up on"):
            Fetcher(url, settings.cache / "x", timeout=0.3, retries=1).text(f"{url}/slow.html", "slow.html")
        assert time.monotonic() - started < 1.4 + 1     # the timeout, plus the pause after a failed try


# -- 4. loops that never end -----------------------------------------------------------------------

def test_an_endless_chain_of_next_pages_is_cut_off(settings, monkeypatch):
    assert scrape_programme.MAX_INDEX_PAGES == 500
    monkeypatch.setattr(scrape_programme, "MAX_INDEX_PAGES", 12)

    def answer(path, query):
        number = int(query.get("page", ["1"])[0])
        return page(f'<a rel="next" href="index.html?page={number + 1}">next</a>')     # always one more

    with site(answer) as (url, asked):
        with pytest.raises(SystemExit, match="went on past 12 pages"):
            scrape_programme.run(url, settings)
    assert len(asked) == 12


def test_a_programme_with_too_many_events_is_cut_off(settings, monkeypatch):
    monkeypatch.setattr(scrape_programme, "MAX_EVENT_PAGES", 30)
    links = "".join(f'<a class="event-link" href="event-{n}.html">x</a>' for n in range(31))
    with site(lambda path, query: page(links)) as (url, asked):
        with pytest.raises(SystemExit, match="more than 30 events"):
            scrape_programme.run(url, settings)
    assert asked == ["/programme/index.html"]


def congress(sessions_answer, days=("2031-03-04",), detail=None):
    """A congress API with one sponsor, whose session list is answered by sessions_answer(query)."""
    def answer(path, query):
        if path == "/api/v1/days":
            return as_json(list(days))
        if path == "/api/v1/sponsors":
            return as_json([{"id": 1, "name": "Skvurmuxne Bygg AB", "tier": "Guld"}])
        if path == "/api/v1/sessions":
            return sessions_answer(query)
        if path.startswith("/api/v1/sessions/") and detail:
            return detail(path)
    return answer


def summary(session_id) -> dict:
    return {"id": session_id, "title": "Session", "type": "talk", "starts_at": "2031-03-04T09:00",
            "ends_at": "2031-03-04T10:00", "sponsor_ids": []}


def full(session_id, **changes) -> dict:
    return {**summary(session_id), "chairs": [{"first_name": "Edit", "last_name": "Snarpvall", "country": "SE",
                                              "affiliation": "Skvurmuxne Bygg AB"}], "talks": [], **changes}


def test_a_page_count_that_always_grows_is_cut_off(settings, monkeypatch):
    assert scrape_congress.MAX_PAGES_PER_DAY == 200
    monkeypatch.setattr(scrape_congress, "MAX_PAGES_PER_DAY", 6)
    api = congress(lambda query: as_json([], {"X-Total-Pages": str(int(query["page"][0]) + 1)}))
    with site(api) as (url, asked):
        with pytest.raises(SystemExit, match="More than 6 is not believed"):
            scrape_congress.run(url, settings)
    assert sum(path.startswith("/api/v1/sessions?") for path in asked) == 6


def test_too_many_days_are_not_believed(settings, monkeypatch):
    monkeypatch.setattr(scrape_congress, "MAX_DAYS", 3)
    api = congress(lambda query: as_json([]), days=[f"2031-03-0{n}" for n in range(1, 5)])
    with site(api) as (url, asked):
        with pytest.raises(SystemExit, match="lists 4 days"):
            scrape_congress.run(url, settings)
    assert not any(path.startswith("/api/v1/sessions") for path in asked)


# -- 5. the session id -----------------------------------------------------------------------------

def test_a_session_id_cannot_climb_out_of_its_place_in_the_address_or_the_cache(settings):
    hostile = ["../../admin/delete", "..%2f..%2fadmin", "7?x=1#y", "a b/c\\d", 7]
    api = congress(lambda query: as_json([summary(i) for i in hostile]),
                   detail=lambda path: as_json(full(path.rsplit("/", 1)[1])))
    with site(api) as (url, asked):
        counts = scrape_congress.run(url, settings)
    details = [path for path in asked if path.startswith("/api/v1/sessions/")]
    assert details == ["/api/v1/sessions/..%2F..%2Fadmin%2Fdelete", "/api/v1/sessions/..%252f..%252fadmin",
                       "/api/v1/sessions/7%3Fx%3D1%23y", "/api/v1/sessions/a%20b%2Fc%5Cd", "/api/v1/sessions/7"]
    assert counts["sessions"] == 5
    files = [path for path in settings.work.rglob("*") if path.is_file()]
    assert inside(settings.work) and all(path.parent in (settings.cache / "congress", settings.output) for path in files)
    assert not any(".." in path.name for path in files)


@pytest.mark.parametrize("session_id", [None, True, 1.5, ["../x"], {"a": 1}, "", ".", "..", "x" * 500])
def test_a_session_id_that_is_not_a_number_or_text_is_refused(settings, session_id):
    api = congress(lambda query: as_json([summary(session_id)]), detail=lambda path: as_json(full(1)))
    with site(api) as (url, asked):
        with pytest.raises(SystemExit, match="session id"):
            scrape_congress.run(url, settings)
    assert not any(path.startswith("/api/v1/sessions/") for path in asked)


@pytest.mark.parametrize("path, body", [
    ("/api/v1/days", {"days": []}), ("/api/v1/days", [1, 2]), ("/api/v1/sponsors", "none"),
    ("/api/v1/sessions", {"error": "no"}), ("/api/v1/sessions", ["a"]), ("/api/v1/sessions/1", [1]),
    ("/api/v1/days", "not json at all"), ("/api/v1/days", "[" * 100_000),
])
def test_an_answer_of_the_wrong_shape_is_refused(settings, path, body):
    honest = congress(lambda query: as_json([summary(1)]), detail=lambda p: as_json(full(1)))

    def answer(asked_path, query):
        if asked_path == path:
            raw = body.encode() if isinstance(body, str) and body != "none" else json.dumps(body).encode()
            return 200, {}, raw
        return honest(asked_path, query)

    with site(answer) as (url, _):
        with pytest.raises(SystemExit, match="did not answer with"):
            scrape_congress.run(url, settings)


# -- 6. the page count -----------------------------------------------------------------------------

@pytest.mark.parametrize("stated, message", [
    ("many", "not a whole number"), ("-1", "not a whole number"), ("1.5", "not a whole number"),
    ("", "not a whole number"), ("٣", "not a whole number"), ("1e3", "not a whole number"),
    ("201", "is not believed"), ("9" * 400, "is not believed"),
])
def test_a_bad_page_count_is_refused_with_a_reason(settings, stated, message):
    api = congress(lambda query: as_json([summary(1)], {"X-Total-Pages": stated.encode("utf-8").decode("latin-1")}),
                   detail=lambda path: as_json(full(1)))
    with site(api) as (url, asked):
        with pytest.raises(SystemExit, match=message):
            scrape_congress.run(url, settings)
    assert sum(path.startswith("/api/v1/sessions?") for path in asked) == 1


@pytest.mark.parametrize("headers, pages_asked", [({}, 1), ({"X-Total-Pages": "0"}, 1), ({"X-Total-Pages": " 2 "}, 2)])
def test_a_missing_page_count_means_one_page_and_zero_ends_the_day(settings, headers, pages_asked):
    api = congress(lambda query: as_json([summary(int(query["page"][0]))], headers), detail=lambda path: as_json(full(1)))
    with site(api) as (url, asked):
        assert scrape_congress.run(url, settings)["sessions"] == pages_asked
    assert sum(path.startswith("/api/v1/sessions?") for path in asked) == pages_asked


# -- 7. addresses that are not http or https -------------------------------------------------------

@pytest.mark.parametrize("address, problem", [
    ("example.org", "no scheme"), ("ftp://x", "ftp:// is not supported"),
    ("file:///etc/passwd", "file:// is not supported"), ("localhost:8765", "no scheme"), ("http://", "no host"),
])
def test_a_base_url_that_cannot_be_scraped_is_refused_in_plain_words(settings, tmp_path, address, problem):
    for run in (scrape_programme.run, scrape_congress.run):
        with pytest.raises(SystemExit, match=problem):
            run(address, settings)
    # And from the command line: an exit with a message, not an exception from the HTTP library.
    config_file = tmp_path / "config" / "settings.toml"
    config_file.parent.mkdir(exist_ok=True)
    config_file.write_text("[scrape]\nretries = 1\n", encoding="utf-8")
    for command in ("scrape", "scrape-congress"):
        with pytest.raises(SystemExit) as refused:
            cli.main(["--config", str(config_file), command, "--base-url", address])
        assert isinstance(refused.value.code, str) and "http" in refused.value.code      # words, not an exit number


def test_a_site_that_cannot_be_reached_or_answers_badly_exits_with_a_message(settings):
    with site(lambda path, query: None) as (url, _):
        with pytest.raises(SystemExit, match="answered 404"):
            scrape_programme.run(url, settings)
    with pytest.raises(SystemExit, match="Gave up on"):        # the server above has stopped: nothing listens there now
        scrape_congress.run(url, settings)


# -- 9. characters no file can hold ----------------------------------------------------------------

def test_control_characters_and_lone_surrogates_in_a_page_do_no_harm(settings):
    def answer(path, query):
        if path == "/programme/index.html":
            return page('<a class="event-link" href="event-1.html">x</a>')
        if path == "/programme/event-1.html":
            body = event_page("1\x00", "Edit\x00 \x07Snarp\x1bvall\x7f\x9f &#xD800;&#x1;", "Skvurmuxne\x0b Bygg\x0c AB\r\n")
            return 200, {}, body.encode("utf-8") + b"\xed\xa0\x80 \xff"      # and bytes that are not UTF-8 at all
    with site(answer) as (url, _):
        scrape_programme.run(url, settings)
    people = read_sheet(settings.output / "participants.xlsx", "All_Participants")
    assert people["Name"].tolist() == ["Edit Snarpvall �"] and people["Organisation"].tolist() == ["Skvurmuxne Bygg AB"]
    assert people["Event_IDs"].tolist() == ["1"]
    for cached in (settings.cache / "programme").iterdir():
        cached.read_text(encoding="utf-8")                      # the cache can be read back


def test_control_characters_and_lone_surrogates_in_json_do_no_harm(settings):
    raw = ('{"id": 1, "title": "Open\\u0000ing \\ud83d", "type": "talk", "starts_at": "2031-03-04T09:00", '
           '"ends_at": "2031-03-04T10:00", "sponsor_ids": [], "talks": [], "chairs": [{"first_name": "Edit\\u0007", '
           '"last_name": "\\udc00Snarpvall", "country": "SE", "affiliation": "Skvurmuxne\\u001f Bygg AB"}]}')
    api = congress(lambda query: as_json([summary(1)]), detail=lambda path: (200, {}, raw.encode("ascii")))
    with site(api) as (url, _):
        assert scrape_congress.run(url, settings)["speakers"] == 1
    speakers = read_sheet(settings.output / "congress.xlsx", "Speakers")
    assert speakers["Person"].tolist() == ["Edit �Snarpvall"]
    assert speakers["Employed_By_Sponsor"].tolist() == ["Skvurmuxne Bygg AB"]
    assert read_sheet(settings.output / "congress.xlsx", "Sessions_Raw")["Title"].tolist() == ["Opening �"]
    for cached in (settings.cache / "congress").iterdir():
        json.loads(cached.read_text(encoding="utf-8"))


def test_write_workbook_takes_characters_openpyxl_refuses(tmp_path):
    every_control = "".join(chr(n) for n in range(0x20)) + "".join(chr(n) for n in range(0x7f, 0xa0))
    values = ["a" + every_control + "b", "x\ud800y", "\udfff", "plain", "\x00", "tab\tand\nline"]
    path = write_workbook(tmp_path / "w.xlsx", {"S": pd.DataFrame({"V\x01": values, "N": range(len(values))})})
    back = read_sheet(path, "S")
    assert back.columns.tolist() == ["V", "N"]
    # Tab and line feed are kept. A carriage return comes back as a line feed: XML has only the one.
    assert back["V"].tolist() == ["a\t\n\nb", "x�y", "�", "plain", "", "tab\tand\nline"]
    assert workbook.clean_text("a\x00b\x85c\ud800") == "abc�"


def test_the_sort_arrow_is_read_in_linear_time():
    assert workbook._label("Account Name  ↑ ") == "Account Name" and workbook._label(" Close Date ↓") == "Close Date"
    assert workbook._label("Amount") == "Amount" and workbook._label("↑") == "" and workbook._label(7) == "7"
    started = time.monotonic()
    assert workbook._label("x" + " " * 400_000 + "x") == "x" + " " * 400_000 + "x"     # hours, with the old pattern
    assert workbook._label(" " * 400_000) == ""
    assert time.monotonic() - started < 2
