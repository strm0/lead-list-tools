"""The mock site is used only when it is serving the dataset on disk.

A pipeline step that finds its port taken must not scrape whatever answers
there: it might be another program, or a mock site left running from a dataset
that has since been generated again.
"""
import json
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from leadlist import config, scrape_programme
from leadlist.mock import generate, server


def _dataset(root, seed):
    settings = config.Settings(root=root, work=root / "work", aliases=root / "config" / "a.yaml",
                               seed=seed, events=12, port=0)
    generate.generate(settings)
    return settings


@pytest.fixture(scope="module")
def ours(tmp_path_factory):
    return _dataset(tmp_path_factory.mktemp("ours"), seed=21)


@pytest.fixture(scope="module")
def theirs(tmp_path_factory):
    return _dataset(tmp_path_factory.mktemp("theirs"), seed=22)


@contextmanager
def serving(httpd):
    """Run a server in the background. Yields its port."""
    threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=3) as answer:
        return json.load(answer)


def test_every_dataset_has_its_own_marker_and_the_server_shows_it(ours, theirs):
    marker = server.read_marker(ours.mock)
    assert marker and marker != server.read_marker(theirs.mock)
    with server.running(ours.mock, ours.host, 0) as url:
        assert _get(f"{url}/api/v1/marker") == {"marker": marker}
        assert server.served_marker(url) == marker
        assert _get(f"{url}/api/v1/days")                    # the rest of the API is as it was


def test_the_marker_is_read_when_the_server_starts(ours, tmp_path):
    """A server left running goes on showing the dataset it started with, not one generated since."""
    copy = _dataset(tmp_path, seed=23)
    started_with = server.read_marker(copy.mock)
    with serving(server.make_server(copy.mock, copy.host, 0)) as port:
        generate.generate(copy)                               # the data is generated again under a running server
        assert server.read_marker(copy.mock) != started_with
        assert server.served_marker(f"http://{copy.host}:{port}") == started_with
        with pytest.raises(SystemExit, match="not by a mock site for this dataset"):
            with server.running(copy.mock, copy.host, port):
                pytest.fail("a mock site for an earlier dataset was used")


def test_another_program_on_the_port_is_refused(ours, tmp_path):
    (tmp_path / "api" / "v1").mkdir(parents=True)
    (tmp_path / "api" / "v1" / "days").write_text('["2031-03-04"]', encoding="utf-8")   # it even answers /api/v1/days
    handler = type("Quiet", (SimpleHTTPRequestHandler,), {"log_message": lambda *args: None})
    foreign = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args: handler(*args, directory=str(tmp_path)))
    with serving(foreign) as port:
        assert _get(f"http://127.0.0.1:{port}/api/v1/days") == ["2031-03-04"]
        with pytest.raises(SystemExit) as refused:
            with server.running(ours.mock, "127.0.0.1", port) as url:
                scrape_programme.run(url, ours)
        message = str(refused.value)
        assert f"Port {port}" in message and "another program" in message and "config/settings.toml" in message
    assert not (ours.output / "participants.xlsx").exists()


@pytest.mark.parametrize("shown", [
    b'{"marker": ""}', b'{"marker": null}', b'{"marker": ["x"]}', b'["x"]', b"not json", b"[" * 3000, b"{}",
])
def test_a_server_that_shows_no_proper_marker_is_refused(ours, shown):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(shown)))
            self.end_headers()
            self.wfile.write(shown)

        def log_message(self, *args) -> None:
            pass

    with serving(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as port:
        assert server.served_marker(f"http://127.0.0.1:{port}") is None
        with pytest.raises(SystemExit, match="not by a mock site for this dataset"):
            with server.running(ours.mock, "127.0.0.1", port):
                pytest.fail("a server without the marker was used")


def test_a_mock_site_for_another_dataset_is_refused(ours, theirs):
    with serving(server.make_server(theirs.mock, theirs.host, 0)) as port:
        with pytest.raises(SystemExit, match="not by a mock site for this dataset") as refused:
            with server.running(ours.mock, ours.host, port):
                pytest.fail("a mock site for another dataset was used")
        assert "started before the mock data was last generated" in str(refused.value)


def test_a_mock_site_for_the_same_dataset_is_used_and_left_running(ours):
    with serving(server.make_server(ours.mock, ours.host, 0)) as port:
        with server.running(ours.mock, ours.host, port) as url:
            assert url == f"http://{ours.host}:{port}"
            assert scrape_programme.run(url, ours)["events"] == 12
        assert server.served_marker(url) == server.read_marker(ours.mock)      # still there: it was not ours to stop


def test_a_dataset_without_a_marker_cannot_borrow_a_server(ours, tmp_path):
    """Data generated before there were markers. There is nothing to compare, so nothing found on the port is trusted."""
    old = _dataset(tmp_path, seed=24)
    (old.mock / server.MARKER_FILE).unlink()
    with serving(server.make_server(old.mock, old.host, 0)) as port:
        with pytest.raises(SystemExit, match="not by a mock site for this dataset"):
            with server.running(old.mock, old.host, port):
                pytest.fail("a server was used with no marker to check it by")
    with server.running(old.mock, old.host, 0) as url:        # its own server, on a free port, still starts
        assert _get(f"{url}/api/v1/marker") == {"marker": ""}


def test_no_mock_data_still_says_so(tmp_path):
    with pytest.raises(SystemExit, match="No mock data yet"):
        with server.running(tmp_path / "mock", "127.0.0.1", 0):
            pass
    with pytest.raises(SystemExit, match="No mock data yet"):
        server.make_server(tmp_path / "mock", "127.0.0.1", 0)
