import pytest
from PySide6.QtCore import QSettings

from aws95.contracts.accounts import RosterEntry
from aws95.core.settings import Settings, is_roster_profile_name


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_setup_flags_default_false(settings):
    assert settings.setup_completed is False and settings.setup_suppress_autostart is False


def test_roster_round_trip_single_signal(settings, qtbot):
    entries = (RosterEntry("dev", "123456789012"), RosterEntry("acct:ReadOnly", "210987654321"))
    keys = []
    settings.changed.connect(keys.append)
    settings.set_roster(entries)
    assert keys == ["accounts/roster"]
    assert settings.roster() == entries


@pytest.mark.parametrize("entries", [
    (RosterEntry("", "123456789012"),),
    (RosterEntry("bad\nname", "123456789012"),),
    (RosterEntry("x" * 129, "123456789012"),),
    (RosterEntry("dev", "123"),),
    (RosterEntry("dev", "123456789012"), RosterEntry("dev", "210987654321")),     # duplicate profile
    (RosterEntry("a", "123456789012"), RosterEntry("b", "123456789012")),         # duplicate account
])
def test_validate_roster_rejects(settings, entries):
    with pytest.raises(ValueError):
        settings.validate_roster(entries)
    with pytest.raises(ValueError):
        settings.set_roster(entries)
    assert settings.roster() == ()


def test_roster_cap(settings):
    with pytest.raises(ValueError):
        settings.set_roster(tuple(RosterEntry(f"p{i}", f"{i:012d}") for i in range(201)))


def test_corrupt_roster_reads_empty(settings):
    settings._qs.setValue("accounts/roster", "{not json")
    assert settings.roster() == ()


def test_roster_skips_invalid_stored_entries(settings):
    settings._qs.setValue("accounts/roster", '[{"profile":"ok","account_id":"123456789012"},'
                                             '{"profile":"","account_id":"1"}, 7,'
                                             '{"profile":"dup","account_id":"123456789012"}]')
    assert settings.roster() == (RosterEntry("ok", "123456789012"),)


@pytest.mark.parametrize("name,ok", [("dev", True), ("acct:Role/x", True), ("has space", True), ("émoji", True),
                                     ("", False), (" ", False), ("a\tb", False), ("x" * 129, False)])
def test_is_roster_profile_name(name, ok):
    assert is_roster_profile_name(name) is ok


def test_known_profiles_round_trip_and_cap(settings):
    settings.set_known_profiles(["a", "b", "a"])
    assert settings.known_profiles() == ("a", "b")
    settings.set_known_profiles([f"p{i}" for i in range(1500)])
    assert len(settings.known_profiles()) == 1000
