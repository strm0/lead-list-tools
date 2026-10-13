"""The scrape cache, and which addresses the fetcher will ask."""
import pytest

from leadlist.fetch import Fetcher, origin

MOCK = "http://127.0.0.1:8765"


def test_a_cached_page_belongs_to_one_address(tmp_path):
    fetcher = Fetcher(MOCK, tmp_path)
    mock = fetcher._path("http://127.0.0.1:8765/programme/index.html", None, "index.html")
    other = fetcher._path("https://programme.example/index.html", None, "index.html")
    assert mock != other and mock == fetcher._path("http://127.0.0.1:8765/programme/index.html", None, "index.html")
    assert fetcher._path("http://x.example/api", {"page": 1}, "s.json") != fetcher._path("http://x.example/api", {"page": 2}, "s.json")


@pytest.mark.parametrize("name", [
    "../../outside.json", "..", ".", "/etc/passwd", "a/b", "a\\b", "session_../../x.json", "nul\x00byte", "",
    "x" * 500, "..%2F..%2Fx", "ünïcode?.json",
])
def test_no_name_from_the_site_chooses_where_the_cache_file_goes(tmp_path, name):
    path = Fetcher(MOCK, tmp_path)._path(MOCK + "/x", None, name)
    assert path.parent == tmp_path and path.resolve().parent == tmp_path.resolve()
    assert ".." not in path.name and "/" not in path.name and "\\" not in path.name and len(path.name) < 100
    path.write_text("ok", encoding="utf-8")          # a name the file system takes


@pytest.mark.parametrize("address, expected", [
    ("http://a.example", ("http", "a.example", 80)),
    ("HTTP://A.Example:80/x y?q=1", ("http", "a.example", 80)),
    ("https://a.example", ("https", "a.example", 443)),
    ("http://a.example:8080/", ("http", "a.example", 8080)),
    ("http://user:secret@a.example@b.example/", ("http", "b.example", 80)),   # the host is what follows the last @
    ("example.org", None), ("ftp://a.example", None), ("file:///etc/passwd", None), ("//a.example/x", None),
    ("javascript:alert(1)", None), ("mailto:someone@a.example", None), ("http://", None), ("http:///x", None),
    ("http://a.example:port/", None), ("http://a.example:99999/", None), ("http://a.example\\@b.example/", None),
    ("http://a.example\n.b.example/", None), ("", None),
])
def test_origin(address, expected):
    assert origin(address) == expected


def test_same_origin_means_scheme_host_and_port(tmp_path):
    fetcher = Fetcher("http://site.example:8080/some/path", tmp_path)
    assert fetcher.same_origin("http://site.example:8080/another/page.html")
    assert fetcher.same_origin("http://SITE.example:8080")
    for elsewhere in ("https://site.example:8080/", "http://site.example/", "http://site.example:8081/",
                      "http://other.example:8080/", "http://site.example.other.example:8080/",
                      "http://site.example:8080@other.example/", "javascript:alert(1)"):
        assert not fetcher.same_origin(elsewhere), elsewhere
    assert Fetcher("https://site.example", tmp_path).same_origin("https://site.example:443/x")


@pytest.mark.parametrize("address, problem", [
    ("example.org", "no scheme"), ("example.org/programme", "no scheme"), ("", "no scheme"),
    ("ftp://files.example", "ftp:// is not supported"), ("file:///etc/passwd", "file:// is not supported"),
    ("http://", "no host"), ("http://site.example:port", "no host"), ("http://[::1", "no host"),
    ("://site.example", "no scheme"),
])
def test_an_address_that_cannot_be_scraped_is_refused_in_plain_words(tmp_path, address, problem):
    with pytest.raises(SystemExit, match=problem) as refused:
        Fetcher(address, tmp_path / "cache")
    assert "http:// or https://" in str(refused.value)
