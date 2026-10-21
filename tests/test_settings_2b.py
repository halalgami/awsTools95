import pytest
from PySide6.QtCore import QSettings

from aws95.core.settings import Settings

A = "123456789012"


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_billing_is_per_account_and_off_by_default(settings):
    seen = []
    settings.changed.connect(seen.append)
    assert settings.account_billing(A) is False
    settings.set_account_billing(A, True)
    assert settings.account_billing(A) is True and settings.account_billing("111111111111") is False
    assert seen == [f"accounts/{A}/billing"]
    with pytest.raises(ValueError):
        settings.set_account_billing("not-an-id", True)
    settings._qs.setValue(f"accounts/{A}/billing", "garbage")
    assert settings.account_billing(A) is False
    assert settings.account_billing("x") is False


def test_activity_retention_days(settings):
    assert settings.activity_retention_days == 90
    settings.activity_retention_days = 100000
    assert settings.activity_retention_days == 3650
