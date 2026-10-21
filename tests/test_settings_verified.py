import json
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QSettings

from aws95.core.settings import MAX_VERIFIED, Settings

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def qs(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def settings(qs, qapp):
    return Settings(qs)


def test_round_trip_and_signal(settings):
    keys = []
    settings.changed.connect(keys.append)
    assert settings.last_verified("dev") is None
    settings.record_verified("dev", "123456789012", NOW)
    assert keys == ["accounts/verified"]
    assert settings.last_verified("dev") == ("123456789012", NOW)


def test_round_trip_survives_a_new_settings_object(settings, qs):
    settings.record_verified("dev", "123456789012", NOW)
    assert Settings(qs).last_verified("dev") == ("123456789012", NOW)


def test_forget_removes_and_signals_once(settings):
    settings.record_verified("dev", "123456789012", NOW)
    keys = []
    settings.changed.connect(keys.append)
    settings.forget_verified("dev")
    assert settings.last_verified("dev") is None and keys == ["accounts/verified"]
    settings.forget_verified("dev")
    assert keys == ["accounts/verified"]


@pytest.mark.parametrize("name,account", [("", "123456789012"), ("bad\nname", "123456789012"),
                                          ("x" * 129, "123456789012"), ("dev", "123"), ("dev", "12345678901x")])
def test_record_rejects_bad_name_or_account(settings, name, account):
    with pytest.raises(ValueError):
        settings.record_verified(name, account, NOW)
    assert settings.last_verified("dev") is None


def test_record_rejects_naive_and_non_utc_time(settings):
    with pytest.raises(ValueError):
        settings.record_verified("dev", "123456789012", NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        settings.record_verified("dev", "123456789012", NOW.astimezone(timezone(timedelta(hours=3))))


def _write(qs, payload):
    qs.setValue("accounts/verified", payload if isinstance(payload, str) else json.dumps(payload))


def test_reading_skips_invalid_entries(settings, qs):
    good = {"account_id": "123456789012", "at": NOW.isoformat()}
    _write(qs, {
        "good": good,
        "bad\nname": good,
        "badacct": {"account_id": "12", "at": NOW.isoformat()},
        "naive": {"account_id": "123456789012", "at": "2026-10-06T12:00:00"},
        "garbage": {"account_id": "123456789012", "at": "not a time"},
        "offset": {"account_id": "123456789012", "at": "2026-10-06T12:00:00+03:00"},
        "notdict": "123456789012",
        "listy": ["x"],
        "numeric": {"account_id": 123456789012, "at": NOW.isoformat()},
    })
    assert settings.last_verified("good") == ("123456789012", NOW)
    for name in ("bad\nname", "badacct", "naive", "garbage", "offset", "notdict", "listy", "numeric"):
        assert settings.last_verified(name) is None


@pytest.mark.parametrize("payload", ["{not json", "[]", '"text"', "null", "42"])
def test_corrupt_store_reads_empty(settings, qs, payload):
    _write(qs, payload)
    assert settings.last_verified("dev") is None
    settings.record_verified("dev", "123456789012", NOW)         # and a corrupt store is replaced, not fatal
    assert settings.last_verified("dev") == ("123456789012", NOW)


def test_cap_keeps_the_newest_entries(settings, qs):
    entry = {"account_id": "123456789012", "at": NOW.isoformat()}
    _write(qs, {f"p{i}": entry for i in range(MAX_VERIFIED + 50)})
    assert settings.last_verified(f"p{MAX_VERIFIED - 1}") is not None
    assert settings.last_verified(f"p{MAX_VERIFIED}") is None            # read side is capped
    settings.record_verified("fresh", "210987654321", NOW)
    assert len(json.loads(str(qs.value("accounts/verified")))) <= MAX_VERIFIED
    assert settings.last_verified("fresh") == ("210987654321", NOW)
