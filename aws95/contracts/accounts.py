from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aws95.contracts.common import AccountRef, Coverage, Status

ProfileKind = Literal["profile", "sso", "assume_role"]
VerifyMode = Literal["auto", "manual", "never"]
NOT_IN_CONFIG = "Not in config any more"     # Profile.issue of a roster profile the config no longer has


@dataclass(frozen=True)
class Profile:
    name: str
    kind: ProfileKind
    region: str | None
    verified: AccountRef | None = None
    selected: bool = False
    issue: str | None = None
    verify: VerifyMode = "auto"
    sign_in_profile: str | None = None


@dataclass(frozen=True)
class ConfigSource:
    path: str
    kind: Literal["config", "credentials"]
    exists: bool
    profile_count: int


@dataclass(frozen=True)
class CapabilityCheck:
    capability: str
    label: str
    status: Status
    missing_actions: tuple[str, ...]
    message: str
    billed: bool
    available: bool


@dataclass(frozen=True)
class RosterEntry:
    profile: str
    account_id: str


@dataclass(frozen=True)
class CollectorHealth:
    collector: str
    status: Status
    error: str | None
    duration_ms: int | None
    coverage: Coverage
