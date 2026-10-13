"""A local server for the mock programme site and the mock congress API.

  /programme/...                              static HTML from work/mock/site
  /api/v1/days                                the dates the congress runs
  /api/v1/sponsors                            every sponsor with its tier
  /api/v1/sessions?date=YYYY-MM-DD&page=N     one day's sessions, paginated
  /api/v1/sessions/<id>                       one session with chairs and talks
  /api/v1/marker                              the mark of the dataset being served

The session list is paginated and reports the page count in an X-Total-Pages
header, the way hosted congress platforms tend to.

Every generated dataset carries a random marker. The server reads it when it
starts and shows it, so that a pipeline step can tell its own mock site from
anything else that happens to be listening on the port.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_PER_PAGE = 10
MARKER_FILE = "run_marker.txt"


class _Handler(SimpleHTTPRequestHandler):
    congress: dict = {}
    marker: str = ""

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path.startswith("/api/v1/"):
            self._api(url.path.removeprefix("/api/v1/").strip("/"), parse_qs(url.query))
        else:
            super().do_GET()

    def _api(self, route: str, query: dict) -> None:
        sessions = self.congress.get("sessions", [])
        if route == "marker":
            return self._json({"marker": self.marker})
        if route == "days":
            return self._json(sorted({s["starts_at"][:10] for s in sessions}))
        if route == "sponsors":
            return self._json([{k: s[k] for k in ("id", "name", "tier")} for s in self.congress.get("sponsors", [])])
        if route == "sessions":
            day = query.get("date", [""])[0]
            try:
                per_page = max(1, int(query.get("per_page", [DEFAULT_PER_PAGE])[0]))
                page = max(1, int(query.get("page", ["1"])[0]))
            except ValueError:
                return self._json({"error": "page and per_page must be whole numbers"}, status=400)
            matching = [s for s in sessions if s["starts_at"].startswith(day)]
            total_pages = max(1, -(-len(matching) // per_page))
            chunk = matching[(page - 1) * per_page: page * per_page]
            summaries = [{k: s[k] for k in ("id", "title", "type", "starts_at", "ends_at", "sponsor_ids")} for s in chunk]
            return self._json(summaries, {"X-Total-Pages": str(total_pages)})
        if route.startswith("sessions/"):
            wanted = route.removeprefix("sessions/")
            for session in sessions:
                if str(session["id"]) == wanted:
                    return self._json(session)
        self._json({"error": "not found"}, status=404)

    def _json(self, payload: object, headers: dict | None = None, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # keep the terminal for the pipeline's own output
        pass


def read_marker(mock_folder: Path) -> str:
    """The marker of the dataset on disk. Empty if it was generated before there were markers."""
    try:
        return (mock_folder / MARKER_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _bind(mock_folder: Path, host: str, port: int) -> ThreadingHTTPServer:
    """Raises OSError if the port cannot be had."""
    site = mock_folder / "site"
    if not site.exists():
        raise SystemExit("No mock data yet. Run: leadlist generate")
    handler = type("Handler", (_Handler,), {
        "congress": json.loads((mock_folder / "api" / "congress.json").read_text(encoding="utf-8")),
        # Read once, at the start. A dataset generated later is another dataset, and this server is not serving it.
        "marker": read_marker(mock_folder),
    })
    return ThreadingHTTPServer((host, port), partial(handler, directory=str(site)))


def make_server(mock_folder: Path, host: str, port: int) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"The mock site is for this machine only. Set host to 127.0.0.1 in the settings, not '{host}'.")
    try:
        return _bind(mock_folder, host, port)
    except OSError as problem:
        raise SystemExit(f"Could not start the mock site on {host}:{port} ({problem.strerror}). "
                         "Is it already running? Change the port in config/settings.toml.") from None


def served_marker(url: str) -> str | None:
    """The marker the server at this address shows. None if it shows none, or is no mock site at all."""
    # No proxy: this is a question to this machine and must not be answered by anything else.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{url}/api/v1/marker", timeout=3) as answer:
            shown = json.loads(answer.read(4096))
    except (OSError, ValueError, RecursionError):
        return None
    marker = shown.get("marker") if isinstance(shown, dict) else None
    return marker if isinstance(marker, str) and marker else None


def _not_ours(host: str, port: int) -> SystemExit:
    return SystemExit(
        f"Port {port} on {host} is taken, and not by a mock site for this dataset. It is another program, "
        "or a mock site started before the mock data was last generated. Nothing was scraped from it. "
        "Stop whatever is listening there (a running leadlist serve-mock: Ctrl+C, then start it again), "
        "or change the port in config/settings.toml.")


@contextmanager
def running(mock_folder: Path, host: str, port: int):
    """Serve in a background thread for as long as the block runs. Yields the base URL.

    If the port is taken, the server found there is used only when it shows the
    marker of the dataset on disk: our own `leadlist serve-mock`, for this data.
    """
    expected = read_marker(mock_folder)
    try:
        server = _bind(mock_folder, host, port)
    except OSError:
        url = f"http://{host}:{port}"
        if not expected or served_marker(url) != expected:
            raise _not_ours(host, port) from None
        yield url
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://{host}:{server.server_address[1]}"
        # The same question to our own server: what answers on this address must be the server just started.
        if expected and served_marker(url) != expected:
            raise _not_ours(host, server.server_address[1])
        yield url
    finally:
        server.shutdown()
        server.server_close()
