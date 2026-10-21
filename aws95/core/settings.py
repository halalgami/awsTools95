"""Typed wrapper over QSettings. Corrupt values fall back to defaults; numbers are clamped."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from collections.abc import Sequence
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

from PySide6.QtCore import QByteArray, QObject, QSettings, Signal
from PySide6.QtGui import QColor

from aws95.contracts.accounts import RosterEntry

T = TypeVar("T")


class ThemeMode(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


def _to_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(raw)


def _clamped_int(lo: int, hi: int) -> Callable[[Any], int]:
    def convert(raw: Any) -> int:
        return max(lo, min(hi, int(raw)))
    return convert


def _choice(allowed: tuple[str, ...]) -> Callable[[Any], str]:
    def convert(raw: Any) -> str:
        value = str(raw)
        if value not in allowed:
            raise ValueError(value)
        return value
    return convert


_ACCOUNT_ID = re.compile(r"[0-9]{12}")
_REGION = re.compile(r"[a-z]{2}(-gov|-iso[a-z]?)?-[a-z]+-[0-9]")
MAX_RECENT_DOCUMENTS = 10
MAX_NICKNAME = 32
MAX_ROSTER = 200
MAX_KNOWN_PROFILES = 1000
MAX_VERIFIED = 1000
MAX_PROFILE_NAME = 128


def is_account_id(text: str) -> bool:
    return bool(_ACCOUNT_ID.fullmatch(text or ""))


def is_roster_profile_name(text: str) -> bool:
    """Roster names (spec §7): non-empty after strip, ≤128 chars, no control characters. Not argv-restricted."""
    if not text or not text.strip() or len(text) > MAX_PROFILE_NAME:
        return False
    return not any(unicodedata.category(c).startswith("C") for c in text)


def is_region(text: str) -> bool:
    return bool(_REGION.fullmatch(text or ""))


def _require_account(account_id: str) -> None:
    if not is_account_id(account_id):
        raise ValueError(f"not a 12-digit account id: {account_id!r}")


class _Setting(Generic[T]):
    def __init__(self, key: str, default: T, convert: Callable[[Any], T]):
        self.key = key
        self.default = default
        self.convert = convert

    def __get__(self, obj: "Settings | None", owner: type | None = None):
        if obj is None:
            return self
        raw = obj._qs.value(self.key, None)
        if raw is None:
            return self.default
        try:
            return self.convert(raw)
        except (ValueError, TypeError):
            return self.default

    def __set__(self, obj: "Settings", value: T) -> None:
        stored = value.value if isinstance(value, Enum) else value
        obj._qs.setValue(self.key, stored)
        obj.changed.emit(self.key)


class Settings(QObject):
    changed = Signal(str)

    theme_mode = _Setting("appearance/theme_mode", ThemeMode.SYSTEM, ThemeMode)
    font_scale = _Setting("appearance/font_scale", 100, _clamped_int(100, 150))
    reduce_motion = _Setting("appearance/reduce_motion", False, _to_bool)
    mac_native_menubar = _Setting("appearance/mac_native_menubar", False, _to_bool)
    sound_muted = _Setting("sound/muted", False, _to_bool)
    sound_volume = _Setting("sound/volume", 80, _clamped_int(0, 100))
    saver_choice = _Setting("saver/choice", "random", str)
    saver_timeout_min = _Setting("saver/timeout_min", 10, _clamped_int(0, 60))
    skip_splash = _Setting("boot/skip_splash", False, _to_bool)
    play_startup_sound = _Setting("boot/play_startup_sound", True, _to_bool)
    wallpaper = _Setting("appearance/wallpaper", "none", _choice(("none", "dots", "bricks")))
    show_tips = _Setting("boot/show_tips", True, _to_bool)
    tips_seen = _Setting("boot/tips_seen", False, _to_bool)
    active_account = _Setting("accounts/active", "", str)
    setup_completed = _Setting("setup/completed", False, _to_bool)
    setup_suppress_autostart = _Setting("setup/suppress_autostart", False, _to_bool)
    activity_retention_days = _Setting("activity/retention_days", 90, _clamped_int(1, 3650))

    def __init__(self, qsettings: QSettings | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._qs = qsettings if qsettings is not None else QSettings("AWS95", "AWS95 Tools")

    # ----- roster: the profiles the user chose to use (spec §7) -----
    def roster(self) -> tuple[RosterEntry, ...]:
        raw = self._qs.value("accounts/roster", None)
        try:
            items = json.loads(str(raw)) if raw is not None else []
        except ValueError:
            return ()
        out: list[RosterEntry] = []
        names: set[str] = set()
        accounts: set[str] = set()
        for item in items if isinstance(items, list) else ():
            if not isinstance(item, dict):
                continue
            name, account = str(item.get("profile", "")), str(item.get("account_id", ""))
            if is_roster_profile_name(name) and is_account_id(account) and name not in names and account not in accounts:
                names.add(name)
                accounts.add(account)
                out.append(RosterEntry(name, account))
        return tuple(out[:MAX_ROSTER])

    def validate_roster(self, entries: Sequence[RosterEntry]) -> None:
        if len(entries) > MAX_ROSTER:
            raise ValueError(f"At most {MAX_ROSTER} accounts can be added.")
        names: set[str] = set()
        accounts: set[str] = set()
        for e in entries:
            if not is_roster_profile_name(e.profile):
                raise ValueError(f"The profile name {e.profile!r} can't be stored.")
            if not is_account_id(e.account_id):
                raise ValueError(f"{e.account_id!r} is not a 12-digit account id.")
            if e.profile in names:
                raise ValueError(f"The profile {e.profile!r} is listed twice.")
            if e.account_id in accounts:
                raise ValueError(f"Account {e.account_id} is listed twice; keep one profile per account.")
            names.add(e.profile)
            accounts.add(e.account_id)

    def set_roster(self, entries: Sequence[RosterEntry]) -> None:
        self.validate_roster(entries)
        payload = json.dumps([{"profile": e.profile, "account_id": e.account_id} for e in entries])
        self._qs.setValue("accounts/roster", payload)
        self.changed.emit("accounts/roster")

    # ----- last successful verification per profile (spec §7): {profile: {account_id, at}} -----
    def _verified_map(self) -> dict[str, tuple[str, datetime]]:
        raw = self._qs.value("accounts/verified", None)
        try:
            items = json.loads(str(raw)) if raw is not None else {}
        except ValueError:
            return {}
        out: dict[str, tuple[str, datetime]] = {}
        for name, item in items.items() if isinstance(items, dict) else ():
            if not isinstance(item, dict) or not isinstance(name, str) or not is_roster_profile_name(name):
                continue
            account, at = item.get("account_id"), item.get("at")
            if not isinstance(account, str) or not is_account_id(account) or not isinstance(at, str):
                continue
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                continue
            if when.tzinfo is None or when.utcoffset() != timedelta(0):
                continue
            out[name] = (account, when.astimezone(timezone.utc))
            if len(out) >= MAX_VERIFIED:
                break
        return out

    def _store_verified(self, data: dict[str, tuple[str, datetime]]) -> None:
        payload = {n: {"account_id": a, "at": t.astimezone(timezone.utc).isoformat()} for n, (a, t) in data.items()}
        self._qs.setValue("accounts/verified", json.dumps(payload))
        self.changed.emit("accounts/verified")

    def last_verified(self, profile: str) -> tuple[str, datetime] | None:
        return self._verified_map().get(profile)

    def record_verified(self, profile: str, account_id: str, at: datetime) -> None:
        if not is_roster_profile_name(profile) or not is_account_id(account_id):
            raise ValueError("not a storable profile or account id")
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise ValueError("verification time must be an aware UTC datetime")
        data = self._verified_map()
        data.pop(profile, None)                 # re-insert last so the newest entries survive the cap
        data[profile] = (account_id, at)
        while len(data) > MAX_VERIFIED:
            del data[next(iter(data))]
        self._store_verified(data)

    def forget_verified(self, profile: str) -> None:
        data = self._verified_map()
        if profile in data:
            del data[profile]
            self._store_verified(data)

    def known_profiles(self) -> tuple[str, ...]:
        raw = self._qs.value("accounts/known_profiles", None)
        try:
            items = json.loads(str(raw)) if raw is not None else []
        except ValueError:
            return ()
        names = [n for n in items if isinstance(n, str) and is_roster_profile_name(n)] if isinstance(items, list) else []
        return tuple(dict.fromkeys(names))[:MAX_KNOWN_PROFILES]

    def set_known_profiles(self, names: Sequence[str]) -> None:
        clean = tuple(dict.fromkeys(n for n in names if is_roster_profile_name(n)))[:MAX_KNOWN_PROFILES]
        self._qs.setValue("accounts/known_profiles", json.dumps(list(clean)))
        self.changed.emit("accounts/known_profiles")

    def geometry(self, program_id: str) -> QByteArray | None:
        raw = self._qs.value(f"geometry/{program_id}", None)
        if isinstance(raw, QByteArray) and not raw.isEmpty():
            return raw
        return None

    def set_geometry(self, program_id: str, data: QByteArray) -> None:
        key = f"geometry/{program_id}"
        self._qs.setValue(key, data)
        self.changed.emit(key)

    def sound_event_enabled(self, name: str, default: bool = True) -> bool:
        raw = self._qs.value(f"sound/event/{name}", None)
        if raw is None:
            return default
        try:
            return _to_bool(raw)
        except ValueError:
            return default

    def set_sound_event_enabled(self, name: str, enabled: bool) -> None:
        key = f"sound/event/{name}"
        self._qs.setValue(key, bool(enabled))
        self.changed.emit(key)

    def icon_position(self, icon_id: str) -> tuple[int, int] | None:
        raw = self._qs.value(f"desktop/icon/{icon_id}", None)
        if raw is None:
            return None
        try:
            x, y = (int(part) for part in str(raw).split(","))
        except ValueError:
            return None
        return (x, y)

    def set_icon_position(self, icon_id: str, x: int, y: int) -> None:
        key = f"desktop/icon/{icon_id}"
        self._qs.setValue(key, f"{int(x)},{int(y)}")
        self.changed.emit(key)

    def clear_icon_positions(self) -> None:
        self._qs.beginGroup("desktop/icon")
        self._qs.remove("")
        self._qs.endGroup()
        self.changed.emit("desktop/icon")

    # ----- accounts (nickname, colour, default regions) -----
    def account_label(self, account_id: str) -> tuple[str, str] | None:
        if not is_account_id(account_id):
            return None
        nick = self._qs.value(f"accounts/{account_id}/nickname", None)
        color = self._qs.value(f"accounts/{account_id}/color", None)
        if nick is None or color is None:
            return None
        nick, color = str(nick).strip(), str(color)
        if not nick or len(nick) > MAX_NICKNAME or not QColor(color).isValid():
            return None
        return (nick, QColor(color).name())

    def set_account_label(self, account_id: str, nickname: str, color: str) -> None:
        _require_account(account_id)
        nick = (nickname or "").strip()
        if not nick or len(nick) > MAX_NICKNAME:
            raise ValueError(f"nickname must be 1..{MAX_NICKNAME} characters")
        if not QColor(color).isValid():
            raise ValueError(f"invalid colour {color!r}")
        self._qs.setValue(f"accounts/{account_id}/nickname", nick)
        self._qs.setValue(f"accounts/{account_id}/color", QColor(color).name())
        self.changed.emit(f"accounts/{account_id}/label")

    def clear_account_label(self, account_id: str) -> None:
        _require_account(account_id)
        self._qs.remove(f"accounts/{account_id}/nickname")
        self._qs.remove(f"accounts/{account_id}/color")
        self.changed.emit(f"accounts/{account_id}/label")

    def insights_model(self, account_id: str) -> str | None:
        if not is_account_id(account_id):
            return None
        value = self._qs.value(f"insights/model/{account_id}", None)
        return str(value) if value else None

    def set_insights_model(self, account_id: str, model_id: str) -> None:
        _require_account(account_id)
        self._qs.setValue(f"insights/model/{account_id}", model_id)
        self.changed.emit(f"insights/model/{account_id}")

    def account_regions(self, account_id: str) -> tuple[str, ...]:
        if not is_account_id(account_id):
            return ()
        raw = self._qs.value(f"accounts/{account_id}/regions", None)
        if raw is None:
            return ()
        parts = raw if isinstance(raw, list) else str(raw).split(",")
        return tuple(p.strip() for p in parts if is_region(str(p).strip()))

    def set_account_regions(self, account_id: str, regions: Sequence[str]) -> None:
        _require_account(account_id)
        bad = [r for r in regions if not is_region(r)]
        if bad:
            raise ValueError(f"invalid region codes: {bad}")
        # Dedupe while keeping first-seen order
        seen = set()
        deduped = []
        for r in regions:
            if r not in seen:
                seen.add(r)
                deduped.append(r)
        self._qs.setValue(f"accounts/{account_id}/regions", ",".join(deduped))
        self.changed.emit(f"accounts/{account_id}/regions")

    def account_billing(self, account_id: str) -> bool:
        """Include Billing (4 billed Cost Explorer requests) in this account's snapshots. Off unless chosen."""
        if not is_account_id(account_id):
            return False
        raw = self._qs.value(f"accounts/{account_id}/billing", None)
        if raw is None:
            return False
        try:
            return _to_bool(raw)
        except ValueError:
            return False

    def set_account_billing(self, account_id: str, on: bool) -> None:
        _require_account(account_id)
        self._qs.setValue(f"accounts/{account_id}/billing", bool(on))
        self.changed.emit(f"accounts/{account_id}/billing")

    # ----- recent documents (Start ▸ Documents) -----
    def recent_documents(self) -> tuple[str, ...]:
        raw = self._qs.value("documents/recent", None)
        if raw is None:
            return ()
        items = [p for p in str(raw).split("\n") if p.strip()]
        return tuple(items[:MAX_RECENT_DOCUMENTS])

    def _store_documents(self, docs: list[str]) -> None:
        self._qs.setValue("documents/recent", "\n".join(docs[:MAX_RECENT_DOCUMENTS]))
        self.changed.emit("documents/recent")

    def add_recent_document(self, path: str) -> None:
        if not path or "\n" in path or "\r" in path:
            raise ValueError("path must not be empty or contain newline/carriage return")
        docs = [p for p in self.recent_documents() if p != path]
        self._store_documents([path, *docs])

    def remove_recent_document(self, path: str) -> None:
        self._store_documents([p for p in self.recent_documents() if p != path])

    def sync(self) -> None:
        self._qs.sync()
