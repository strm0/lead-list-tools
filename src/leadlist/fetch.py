"""Polite, cached HTTP for the scrapers.

A scraper is pointed at one site and stays there. What comes back is treated
as hostile: a link or a redirect to another site is refused, a body is read
up to a limit, and nothing the site sends chooses where a file is written.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

from .workbook import clean_text

USER_AGENT = "lead-list-tools (demo scraper)"
MAX_BODY_BYTES = 5 * 1024 * 1024    # one HTML page or one JSON answer
MAX_REDIRECTS = 5
MAX_READ_SECONDS = 120.0            # for one body, however slowly the server feeds it
_DEFAULT_PORTS = {"http": 80, "https": 443}
_REDIRECTS = (301, 302, 303, 307, 308)
_UNSAFE_IN_FILE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_DOTS = re.compile(r"\.{2,}")
# A backslash or a control character in an address is read differently by different parsers.
_UNSAFE_IN_ADDRESS = re.compile(r"[\\\x00-\x1f\x7f]")


def origin(url: str) -> tuple[str, str, int] | None:
    """(scheme, host, port) of an http or https address. None for anything else."""
    if not isinstance(url, str) or _UNSAFE_IN_ADDRESS.search(url):
        return None
    try:
        # The parser requests itself connects with, so that the host checked here is the host that is asked.
        parts = parse_url(url)
    except (LocationParseError, ValueError):
        return None
    scheme, host = (parts.scheme or "").lower(), (parts.host or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    return scheme, host, parts.port or _DEFAULT_PORTS[scheme]


def _scrub(value: object) -> object:
    """Clean every string in a decoded JSON body. JSON can carry lone surrogates and control characters."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, dict):
        return {_scrub(key): _scrub(item) for key, item in value.items()}
    return value


class Fetcher:
    """Fetches each URL once, keeps the response on disk, waits between requests.

    It is given the address it was pointed at and asks nothing of any other
    scheme, host or port.
    """

    def __init__(self, base_url: str, cache: Path, delay: float = 0.0, timeout: float = 20.0,
                 retries: int = 3) -> None:
        self.origin = origin(base_url)
        if self.origin is None:
            scheme = base_url.split("://", 1)[0].lower() if isinstance(base_url, str) and "://" in base_url else ""
            if not scheme:
                problem = "it has no scheme"
            elif scheme not in _DEFAULT_PORTS:
                problem = f"the scheme {scheme}:// is not supported"
            else:
                problem = "it names no host, or is not a well-formed address"
            raise SystemExit(f"Cannot scrape {base_url!r}: {problem}. "
                             "Give the address in full, starting with http:// or https://")
        self.cache, self.delay, self.timeout, self.retries = cache, delay, timeout, retries
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.requests_made = 0

    def same_origin(self, url: str) -> bool:
        """True if the address is on the scheme, host and port this fetcher was pointed at."""
        return origin(url) == self.origin

    def _read(self, response: requests.Response, url: str) -> bytes:
        """The body, up to the limit. A longer one is refused, not cut short."""
        too_big = SystemExit(f"{url} sent more than {MAX_BODY_BYTES // (1024 * 1024)} MB. "
                             "That is not a programme page; nothing was scraped.")
        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise too_big
        started, body = time.monotonic(), bytearray()
        # The limit counts the bytes after decompression, so a small compressed answer cannot unpack into a huge one.
        for chunk in response.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise too_big
            if time.monotonic() - started > MAX_READ_SECONDS:
                raise SystemExit(f"{url} was still sending after {MAX_READ_SECONDS:.0f} seconds. Nothing was scraped.")
        return bytes(body)

    def _once(self, url: str, params: dict | None) -> tuple[bytes, dict]:
        """One address, following redirects by hand so that none leaves the site."""
        for _ in range(MAX_REDIRECTS + 1):
            # Built first and checked as it will be sent: what is checked is what is asked for.
            prepared = requests.Request("GET", url, params=params).prepare()
            if not self.same_origin(prepared.url):
                raise SystemExit(f"Refused to fetch {url}: it is not on the site being scraped "
                                 f"({self.origin[0]}://{self.origin[1]}:{self.origin[2]}).")
            response = self.session.get(prepared.url, timeout=self.timeout, allow_redirects=False, stream=True)
            try:
                if response.status_code >= 500:
                    raise requests.ConnectionError(f"server answered {response.status_code}")
                if response.status_code in _REDIRECTS and response.headers.get("Location"):
                    target = urljoin(prepared.url, response.headers["Location"])
                    if not self.same_origin(target):
                        raise SystemExit(f"{url} redirects to another site ({target[:200]}). "
                                         "The redirect was not followed and nothing was scraped.")
                    url, params = target, None
                    continue
                if response.status_code != 200:
                    raise SystemExit(f"{url} answered {response.status_code}. Nothing was scraped.")
                headers = {name.lower(): value for name, value in response.headers.items()}
                return self._read(response, url), headers
            finally:
                response.close()
        raise SystemExit(f"{url} redirected more than {MAX_REDIRECTS} times. Nothing was scraped.")

    def _get(self, url: str, params: dict | None) -> tuple[bytes, dict]:
        """Returns the body and the response headers, header names in lower case."""
        problem: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                answer = self._once(url, params)
                self.requests_made += 1
                time.sleep(self.delay)
                return answer
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as error:
                problem = error
            except (requests.RequestException, ValueError) as error:
                # A missing or unsupported scheme, an address that cannot be parsed, a broken compressed body.
                raise SystemExit(f"Could not fetch {url}: {error}") from None
            time.sleep(attempt)  # back off a little longer each time
        raise SystemExit(f"Gave up on {url} after {self.retries} tries: {problem}")

    def _path(self, url: str, params: dict | None, name: str) -> Path:
        """One cache file per address, so that a page from one site is never served for another."""
        address = url + json.dumps(params or {}, sort_keys=True, default=str)
        # The name is for a person reading the folder. It may come from the site, so only plain
        # characters are kept, and no two dots in a row. The hash in front is what tells files apart.
        readable = _DOTS.sub(".", _UNSAFE_IN_FILE_NAME.sub("_", name or "")).lstrip(".")[:60] or "page"
        return self.cache / f"{hashlib.sha256(address.encode('utf-8', 'replace')).hexdigest()[:10]}_{readable}"

    def text(self, url: str, cache_name: str) -> str:
        path = self._path(url, None, cache_name)
        if path.exists():
            return path.read_text(encoding="utf-8")
        body, _ = self._get(url, None)
        text = body.decode("utf-8", errors="replace")
        path.write_text(text, encoding="utf-8")
        return text

    def json(self, url: str, params: dict | None, cache_name: str) -> tuple[object, dict]:
        """Returns the decoded body and the response headers."""
        path = self._path(url, params, cache_name)
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            return stored["body"], stored["headers"]
        raw, headers = self._get(url, params)
        try:
            body = _scrub(json.loads(raw))
        except (ValueError, RecursionError):
            raise SystemExit(f"{url} did not answer with JSON that can be read. Nothing was scraped.") from None
        # Header names in lower case: servers and proxies do not agree on their spelling.
        stored = {"headers": {name: clean_text(value) for name, value in headers.items()}, "body": body}
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
        return stored["body"], stored["headers"]
