"""Settings: paths, mock data size and the column names of the CRM exports."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class MeetingColumns:
    file: str = "meetings_export.xlsx"
    account: str = "Company / Account"
    contact: str = "Contact"
    subject: str = "Subject"
    date: str = "Date"
    left_tag: str = "LEFT"


@dataclass(frozen=True)
class WonColumns:
    file: str = "won_export.xlsx"
    account: str = "Account Name"
    source: str = "Lead Source"
    opportunity: str = "Opportunity Name"
    amount: str = "Amount"
    close_date: str = "Close Date"


@dataclass(frozen=True)
class Settings:
    root: Path
    work: Path
    aliases: Path
    seed: int = 2026
    events: int = 300
    host: str = "127.0.0.1"
    port: int = 8765
    delay_seconds: float = 0.0
    timeout_seconds: float = 20.0
    retries: int = 3
    meetings: MeetingColumns = MeetingColumns()
    won: WonColumns = WonColumns()

    @property
    def mock(self) -> Path:
        return self.work / "mock"

    @property
    def cache(self) -> Path:
        return self.work / "cache"

    @property
    def output(self) -> Path:
        return self.work / "output"

    @property
    def state(self) -> Path:
        return self.work / "state"

    @property
    def meetings_file(self) -> Path:
        return self.mock / "crm" / self.meetings.file

    @property
    def won_file(self) -> Path:
        return self.mock / "crm" / self.won.file

    @property
    def truth_file(self) -> Path:
        return self.mock / "answer_key.json"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def with_changes(self, **changes) -> "Settings":
        return replace(self, **changes)


def load(config_file: Path | None = None) -> Settings:
    """Read config/settings.toml. Without one, defaults apply to the current folder."""
    config_file = config_file or Path("config") / "settings.toml"
    if not config_file.exists():
        root = Path.cwd()
        return Settings(root=root, work=root / "work", aliases=root / "config" / "org_aliases.yaml")
    with open(config_file, "rb") as handle:
        raw = tomllib.load(handle)
    root = config_file.resolve().parent.parent
    paths, mock, scrape = raw.get("paths", {}), raw.get("mock", {}), raw.get("scrape", {})
    crm = raw.get("crm", {})
    work = (root / paths.get("work", "work")).resolve()
    aliases = (root / paths.get("aliases", "config/org_aliases.yaml")).resolve()
    for label, path in (("work", work), ("aliases", aliases)):
        # These are written to and, for work, deleted from. A settings file must not aim them elsewhere.
        if not path.is_relative_to(root) or path == root:
            raise SystemExit(f"{config_file}: paths.{label} must lie inside {root}, not at {path}")
    return Settings(
        root=root,
        work=work,
        aliases=aliases,
        seed=int(mock.get("seed", 2026)),
        events=int(mock.get("events", 300)),
        host=mock.get("host", "127.0.0.1"),
        port=int(mock.get("port", 8765)),
        delay_seconds=float(scrape.get("delay_seconds", 0.0)),
        timeout_seconds=float(scrape.get("timeout_seconds", 20.0)),
        retries=int(scrape.get("retries", 3)),
        meetings=MeetingColumns(**crm.get("meetings", {})),
        won=WonColumns(**crm.get("won", {})),
    )
