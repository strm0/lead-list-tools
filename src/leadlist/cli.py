"""The command line: one entry point, one subcommand per step."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import (
    config, crossref, municipalities, municipality_filter, report, report_page, review, scrape_congress,
    scrape_programme,
)
from .mock import generate as mock_generate
from .mock import reviewer as mock_reviewer
from .mock import server as mock_server


def _at_least_one(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _heading(text: str) -> None:
    print(f"\n{text}")


def _with_site(settings: config.Settings, base_url: str | None, step) -> None:
    """Run a scraping step against base_url, or against the mock site served for the occasion."""
    if base_url:
        address = urlsplit(base_url)
        if address.scheme not in ("http", "https") or not address.netloc:
            raise SystemExit(f"--base-url must be a full web address such as https://programme.example, not '{base_url}'.")
        step(base_url.rstrip("/"))
    else:
        with mock_server.running(settings.mock, settings.host, settings.port) as url:
            step(url)


def cmd_generate(settings: config.Settings, args) -> None:
    _heading("Generating mock data")
    for label, value in mock_generate.generate(settings, reset_aliases=args.reset_aliases).items():
        print(f"  {label:20s} {value}")
    print(f"  wrote {settings.mock}")


def cmd_serve_mock(settings: config.Settings, args) -> None:
    server = mock_server.make_server(settings.mock, settings.host, settings.port)
    print(f"Mock programme: {settings.base_url}/programme/index.html")
    print(f"Mock congress API: {settings.base_url}/api/v1/sponsors\nCtrl+C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


def cmd_scrape(settings: config.Settings, args) -> None:
    _heading("Scraping the programme")
    _with_site(settings, args.base_url, lambda url: scrape_programme.run(url, settings))


def cmd_scrape_congress(settings: config.Settings, args) -> None:
    _heading("Scraping the congress API")
    _with_site(settings, args.base_url, lambda url: scrape_congress.run(url, settings))


def cmd_filter(settings: config.Settings, args) -> None:
    if args.self_test:
        raise SystemExit(0 if municipalities.self_test() else 1)
    _heading("Municipality filter")
    municipality_filter.run(settings)


def cmd_phase1(settings: config.Settings, args) -> None:
    _heading("Cross-reference, phase one")
    crossref.phase_one(settings)


def cmd_review(settings: config.Settings, args) -> None:
    review.run(settings, args.port, open_browser=not args.no_open)


def _report(settings: config.Settings) -> None:
    _heading("Run report")
    latest = report.build(settings)
    report.print_summary(latest)
    print(f"\n  wrote {report_page.write(settings, latest)}")


def cmd_phase2(settings: config.Settings, args) -> None:
    _heading("Cross-reference, phase two")
    crossref.phase_two(settings, accept_undecided=args.accept_undecided)
    _report(settings)


def cmd_report(settings: config.Settings, args) -> None:
    _report(settings)


def cmd_demo(settings: config.Settings, args) -> None:
    """The whole pipeline on mock data, with a stand-in for the person at the stop."""
    args.reset_aliases = True    # the demo always starts from this dataset's own starting table
    cmd_generate(settings, args)
    with mock_server.running(settings.mock, settings.host, settings.port) as url:
        _heading("Scraping the programme")
        scrape_programme.run(url, settings)
        _heading("Scraping the congress API")
        scrape_congress.run(url, settings)
    cmd_filter(settings, args)
    cmd_phase1(settings, args)
    if args.stop_at_review:
        return

    _heading("The demo carries on past the stop. On real data it would not.")
    _heading("Stand-in reviewer (reads the answer key, as a person would read the audit)")
    for label, value in mock_reviewer.review_from_answer_key(settings).items():
        print(f"  {label:22s} {value}")
    cmd_phase2(settings, args)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="leadlist",
        description="Scrape a programme, match it against CRM exports, write lead lists. Runs on mock data.",
    )
    parser.add_argument("--config", type=Path, help="settings file (default: config/settings.toml)")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, summary: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=summary, description=summary)
        sub.set_defaults(handler=handler, reset_aliases=False, self_test=False, seed=None, events=None,
                         stop_at_review=False, accept_undecided=False)
        return sub

    for name, handler, summary in (
        ("generate", cmd_generate, "write the mock programme, congress API data, CRM exports and answer key"),
        ("demo", cmd_demo, "the whole pipeline on mock data, with a stand-in reviewer at the stop"),
    ):
        sub = add(name, handler, summary)
        sub.add_argument("--seed", type=int, help="seed for the generator (default from settings)")
        sub.add_argument("--events", type=_at_least_one, help="number of events to invent (default from settings)")
        if name == "generate":
            sub.add_argument("--reset-aliases", action="store_true",
                             help="overwrite the alias table with the starting one for this seed")
        if name == "demo":
            sub.add_argument("--stop-at-review", action="store_true",
                             help="stop after phase one, so that you can do the review yourself")
    add("serve-mock", cmd_serve_mock, "serve the mock site and API until stopped")
    for name, handler, summary in (
        ("scrape", cmd_scrape, "read every event page and write one row per named person"),
        ("scrape-congress", cmd_scrape_congress, "read speakers and sponsors from the congress API"),
    ):
        add(name, handler, summary).add_argument("--base-url", help="scrape this site instead of the local mock")
    add("filter", cmd_filter, "sort people by municipality: matched, rejected, parse issues").add_argument(
        "--self-test", action="store_true", help="check the classifier against known cases and exit")
    add("phase1", cmd_phase1, "normalise, list possible collisions, write the audit workbook and stop")
    sub = add("review", cmd_review, "open the review screen for the possible collisions")
    sub.add_argument("--port", type=int, default=8766, help="port for the review screen (default 8766)")
    sub.add_argument("--no-open", action="store_true", help="do not open the browser")
    add("phase2", cmd_phase2, "write the lead lists, after a person has reviewed the collisions").add_argument(
        "--accept-undecided", action="store_true",
        help="run although some possible collisions have no decision; they stand as the normaliser has them")
    add("report", cmd_report, "count what the latest run produced and how much of it was right")

    args = parser.parse_args(argv)
    if args.config and not args.config.exists():
        print(f"warning: {args.config} does not exist; using built-in settings for the current folder.", file=sys.stderr)
    elif not args.config and not (Path("config") / "settings.toml").exists():
        print("warning: no config/settings.toml here. This does not look like the repository root; "
              "built-in settings are used and files are written under ./work.", file=sys.stderr)
    settings = config.load(args.config)
    overrides = {name: getattr(args, name) for name in ("seed", "events") if getattr(args, name) is not None}
    args.handler(settings.with_changes(**overrides) if overrides else settings, args)


if __name__ == "__main__":
    main()
