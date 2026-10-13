"""The review screen for the stop between phase one and phase two.

A small local server and one page. The page shows each possible collision
with the spellings from the programme and the CRM side by side. The person
says "same organisation" or "keep apart", and each decision is written to the
alias table at once. Editing that file by hand works as before; the page
reads it again every time it loads its state.

The screen never starts phase two. It shows the command; a person runs it.
"""
from __future__ import annotations

import copy
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from .config import Settings
from .crossref import find_collisions, load_meetings, load_participants, load_won
from .normalize import AFFIX_TOKENS, AliasTable, base_key, clean
from .review_page import PAGE

MAX_BODY_BYTES = 1024 * 1024    # a decision is a few hundred bytes
# The page carries its own script and style and asks only this server for data.
# Nothing else may load, and no other page may put this one in a frame.
PAGE_POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
               "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
DATA_POLICY = "default-src 'none'; frame-ancestors 'none'"


def explain(spelling: str) -> list[list[str]]:
    """Split a spelling into parts, marking what the normaliser sets aside.

    Returns [text, kind] pairs. kind is "kept", "dropped" (a company form or
    similar, removed from either end) or "same" ("&" and "och", read alike).
    """
    words = spelling.split()
    cleaned = [clean(word).split() for word in words]
    flat = [token for tokens in cleaned for token in tokens]
    base = base_key(spelling).split()
    front = next((i for i in range(len(flat) - len(base) + 1) if flat[i:i + len(base)] == base), 0)
    back_from = front + len(base)

    parts, position = [], 0
    for word, tokens in zip(words, cleaned):
        start, position = position, position + len(tokens)
        if tokens and all(t in AFFIX_TOKENS for t in tokens) and (position <= front or start >= back_from):
            kind = "dropped"
        elif tokens == ["och"]:
            kind = "same"
        else:
            kind = "kept"
        parts.append([word, kind])
    return parts


def _count(number: int, unit: str) -> str:
    return f"{number} {unit}" + ("" if number == 1 else "s")


class ReviewSession:
    """What the page can ask for and do. No HTTP in here, so it can be tested directly."""

    def __init__(self, settings: Settings) -> None:
        if not (settings.state / "phase1.json").exists():
            raise SystemExit("Nothing to review yet. Run: leadlist phase1")
        self.settings = settings
        empty = AliasTable()
        self._people = load_participants(settings, empty)
        self._meetings = load_meetings(settings, empty)
        self._won = load_won(settings, empty)
        self._known = (set(self._people["Organisation"]) | set(self._meetings["account_raw"])
                       | set(self._won["account_raw"]))
        self._history: list[AliasTable] = []
        self._last_written: str | None = None      # the alias file as this session last left it
        self._lock = threading.Lock()

    # -- reading ------------------------------------------------------------

    def _table(self) -> AliasTable:
        return AliasTable.load(self.settings.aliases)

    def _keyed(self, table: AliasTable) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        key = lambda raw: table.key(raw).key  # noqa: E731
        return (self._people.assign(org_key=self._people["Organisation"].map(key)),
                self._meetings.assign(account_key=self._meetings["account_raw"].map(key)),
                self._won.assign(account_key=self._won["account_raw"].map(key)))

    def state(self) -> dict:
        table = self._table()
        people, meetings, won = self._keyed(table)
        meeting_groups = {key: group for key, group in meetings.groupby("account_key")}
        deal_counts = won.groupby("account_key").size()

        def context(key: str) -> str:
            notes = []
            if key in meeting_groups:
                group = meeting_groups[key]
                latest = group["date"].max()
                notes.append(_count(len(group), "meeting") + ("" if pd.isna(latest) else f", latest {latest.date()}"))
            if key in deal_counts.index:
                notes.append(_count(deal_counts[key], "won deal"))
            return ". ".join(notes) or "No history"

        to_review = []
        for collision in find_collisions(people, meetings, won, table):
            if collision.decided:
                continue
            crm = sorted(set(collision.meetings) | set(collision.won))
            forms: dict[str, list[str]] = {}
            for spelling in collision.spellings + collision.kept_apart:
                if spelling not in forms.setdefault(clean(spelling), []):
                    forms[clean(spelling)].append(spelling)
            to_review.append({
                "key": collision.key,
                "programme": [{"text": s, "parts": explain(s)} for s in collision.programme],
                "crm": [{"text": s, "parts": explain(s)} for s in crm],
                "people": collision.people,
                "context": context(collision.key),
                "spellings": collision.spellings,
                "forms": list(forms.values()),
                # An undecided spelling that looks like names already kept apart: the person says which it is.
                "kept_apart": collision.kept_apart,
                "canonical": (collision.meetings or collision.won or collision.programme)[0],
            })

        crm_keys = set(meetings["account_key"]) | set(won["account_key"])
        programme_keys = set(people["org_key"])
        headcount = people[~people["org_key"].isin(crm_keys) & (people["org_key"] != "")].groupby(
            "Organisation")["person_key"].nunique()
        lonely_accounts = {}
        for frame, unit in ((meetings, "meeting"), (won, "won deal")):
            for name, number in frame[~frame["account_key"].isin(programme_keys)].groupby("account_raw").size().items():
                lonely_accounts.setdefault(name, []).append(_count(number, unit))

        return {
            "phase_two_ran": (self.settings.state / "phase2.json").exists(),
            "alias_file": str(self.settings.aliases.relative_to(self.settings.root))
            if self.settings.aliases.is_relative_to(self.settings.root) else str(self.settings.aliases),
            "to_review": to_review,
            "decided": (
                [{"kind": "same", "name": name, "variants": variants} for name, variants in sorted(table.aliases.items())]
                + [{"kind": "apart", "name": name, "variants": []} for name in sorted(table.keep_apart)]),
            "unmatched": {
                "programme": [{"name": name, "note": f"{n} {'person' if n == 1 else 'people'}"}
                              for name, n in headcount.sort_values(ascending=False).items()],
                "crm": [{"name": name, "note": ", ".join(notes)} for name, notes in sorted(lonely_accounts.items())],
            },
            "can_undo": bool(self._history),
        }

    # -- deciding -----------------------------------------------------------

    def _check(self, *spellings: str) -> None:
        table = self._table()
        listed = set(table.keep_apart) | set(table.aliases)
        unknown = [s for s in spellings if s not in self._known and s not in listed]
        if unknown or not spellings:
            raise ValueError(f"not a spelling from this run: {unknown or 'nothing given'}")

    def _change(self, edit) -> None:
        with self._lock:
            table = self._table()
            earlier = copy.deepcopy(table)
            edit(table)                      # raises ValueError if the table would contradict itself
            table.save(self.settings.aliases)
            self._history.append(earlier)
            self._last_written = self.settings.aliases.read_text(encoding="utf-8")

    def same(self, spellings: list[str], canonical: str) -> None:
        """These spellings are one organisation."""
        self._check(canonical, *spellings)
        self._change(lambda table: table.add_alias(canonical, [s for s in spellings if s != canonical]))

    def apart(self, groups: list[list[str]]) -> None:
        """Each group is a different organisation, whatever the normaliser makes of the names."""
        groups = [group for group in groups if group]
        self._check(*[s for group in groups for s in group])
        if len(groups) < 2:
            raise ValueError("keeping apart needs at least two organisations")

        def edit(table: AliasTable) -> None:
            already = {clean(name) for name in table.keep_apart}
            # A name that is already kept apart leads its group; the rest become its spellings.
            groups[:] = [sorted(group, key=lambda s: clean(s) not in already) for group in groups]
            table.add_keep_apart([group[0] for group in groups])
            for group in groups:
                others = [s for s in group[1:] if clean(s) != clean(group[0])]
                if others:
                    table.add_alias(group[0], others)
        self._change(edit)

    def link(self, programme: str, crm: str) -> None:
        """A spelling from the programme is the organisation filed under this CRM account."""
        self._check(programme, crm)
        self._change(lambda table: table.add_alias(crm, [programme]))

    def forget(self, kind: str, name: str) -> None:
        """Take one entry back out of the alias table."""
        def edit(table: AliasTable) -> None:
            if kind == "same" and name in table.aliases:
                del table.aliases[name]
            elif kind == "apart" and name in table.keep_apart:
                table.keep_apart.remove(name)
            else:
                raise ValueError("no such entry in the alias table")
            table._index()
        self._change(edit)

    def undo(self) -> None:
        """Put the alias table back as it was before the latest change made here."""
        with self._lock:
            if not self._history:
                return
            if self.settings.aliases.read_text(encoding="utf-8") != self._last_written:
                self._history.clear()
                raise ValueError("the alias file has been edited by hand since; undoing here would overwrite that")
            self._history.pop().save(self.settings.aliases)
            self._last_written = self.settings.aliases.read_text(encoding="utf-8")


def make_server(settings: Settings, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Returns the server and the address of the page. Listens on this machine only."""
    session = ReviewSession(settings)
    token = secrets.token_urlsafe(16)   # so that no other page in the browser can post decisions
    actions = {
        "same": lambda body: session.same(body["spellings"], body["canonical"]),
        "apart": lambda body: session.apart(body["groups"]),
        "link": lambda body: session.link(body["programme"], body["crm"]),
        "forget": lambda body: session.forget(body["kind"], body["name"]),
        "undo": lambda body: session.undo(),
    }

    class Handler(BaseHTTPRequestHandler):
        _policy = DATA_POLICY

        def end_headers(self) -> None:
            # Here, so that every answer carries them, the error pages the base class writes included.
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", self._policy)
            self.send_header("X-Content-Type-Options", "nosniff")
            super().end_headers()

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self._policy = PAGE_POLICY if kind.startswith("text/html") else DATA_POLICY
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: object, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _local(self) -> bool:
            """Only this machine's own address. Stops another site's page reaching the screen through DNS tricks."""
            port = self.server.server_address[1]
            if self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return True
            self._json({"error": "this screen answers on 127.0.0.1 only"}, 403)
            return False

        def do_GET(self) -> None:
            if not self._local():
                return
            if self.path == "/":
                self._send(200, PAGE.replace("__TOKEN__", token).encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            elif self.path == "/api/state":
                try:
                    self._json(session.state())
                except SystemExit as problem:      # the alias file cannot be read as it stands
                    self._json({"error": str(problem)}, 500)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            if not self._local():
                return
            action = actions.get(self.path.removeprefix("/api/"))
            if action is None:
                return self._json({"error": "not found"}, 404)
            if self.headers.get("X-Review-Token") != token:
                return self._json({"error": "this page is out of date; reload it"}, 403)
            # The length is checked before a byte of the body is read. The body is left unread
            # on a refusal, so the connection is closed after the answer.
            stated = self.headers.get("Content-Length", "0").strip()
            if not (stated.isascii() and stated.isdigit()):
                self.close_connection = True
                return self._json({"error": "Content-Length must be a whole number, zero or more"}, 400)
            if len(stated) > 12 or int(stated) > MAX_BODY_BYTES:
                self.close_connection = True
                return self._json({"error": "the request is too large"}, 413)
            try:
                action(json.loads(self.rfile.read(int(stated)) or b"{}"))
                self._json(session.state())
            except (ValueError, KeyError, TypeError) as problem:
                self._json({"error": str(problem)}, 400)
            except RecursionError:      # JSON nested deeper than Python will follow
                self._json({"error": "the request is nested too deeply"}, 400)
            except SystemExit as problem:
                self._json({"error": str(problem)}, 500)

        def log_message(self, *args) -> None:
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as problem:
        raise SystemExit(f"Could not start the review screen on port {port} ({problem.strerror}). "
                         "Try: leadlist review --port 8767") from None
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def run(settings: Settings, port: int, open_browser: bool = True) -> None:
    server, url = make_server(settings, port)
    print(f"Review screen: {url}")
    print(f"Decisions are written to {settings.aliases} as you make them.")
    print("This screen does not start phase two. When you have read them all: leadlist phase2")
    print("Ctrl+C stops the screen.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
