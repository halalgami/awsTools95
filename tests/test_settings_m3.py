import pytest
from PySide6.QtCore import QSettings

from aws95.contracts.activity import TopDimension
from aws95.core.settings import MAX_RECENT_DOCUMENTS, Settings, is_account_id, is_region


@pytest.fixture
def settings(tmp_path, qapp):
    return Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))


def test_account_id_and_region_validation():
    assert is_account_id("111111111111")
    assert not is_account_id("11111111111") and not is_account_id("1111111111a1") and not is_account_id("../x")
    assert is_region("us-east-1") and is_region("me-south-1") and is_region("us-gov-west-1")
    assert not is_region("US-EAST-1") and not is_region("us-east") and not is_region("")


def test_account_label_round_trip(settings, qtbot):
    assert settings.account_label("111111111111") is None
    with qtbot.waitSignal(settings.changed) as blocker:
        settings.set_account_label("111111111111", "  lab  ", "#ffd34d")
    assert blocker.args == ["accounts/111111111111/label"]
    assert settings.account_label("111111111111") == ("lab", "#ffd34d")
    settings.clear_account_label("111111111111")
    assert settings.account_label("111111111111") is None


@pytest.mark.parametrize("acct,nick,color", [
    ("12345", "x", "#000000"),            # bad id
    ("111111111111", "   ", "#000000"),   # empty nickname
    ("111111111111", "x" * 33, "#000000"),
    ("111111111111", "x", "not-a-colour"),
])
def test_account_label_rejects_bad_input(settings, acct, nick, color):
    with pytest.raises(ValueError):
        settings.set_account_label(acct, nick, color)


def test_corrupt_stored_label_reads_as_none(settings):
    settings._qs.setValue("accounts/111111111111/nickname", "lab")
    settings._qs.setValue("accounts/111111111111/color", "zzz")
    assert settings.account_label("111111111111") is None


def test_account_regions(settings):
    assert settings.account_regions("111111111111") == ()
    settings.set_account_regions("111111111111", ["us-east-1", "eu-west-1"])
    assert settings.account_regions("111111111111") == ("us-east-1", "eu-west-1")
    with pytest.raises(ValueError):
        settings.set_account_regions("111111111111", ["us-east-1", "mars-1"])
    settings._qs.setValue("accounts/111111111111/regions", "us-east-1,garbage")
    assert settings.account_regions("111111111111") == ("us-east-1",)


def test_recent_documents_dedupe_and_cap(settings, qtbot):
    for i in range(MAX_RECENT_DOCUMENTS + 3):
        settings.add_recent_document(f"/tmp/r{i}.md")
    docs = settings.recent_documents()
    assert len(docs) == MAX_RECENT_DOCUMENTS and docs[0] == f"/tmp/r{MAX_RECENT_DOCUMENTS + 2}.md"
    with qtbot.waitSignal(settings.changed) as blocker:
        settings.add_recent_document("/tmp/r5.md")
    assert blocker.args == ["documents/recent"]
    assert settings.recent_documents()[0] == "/tmp/r5.md"
    assert settings.recent_documents().count("/tmp/r5.md") == 1
    settings.remove_recent_document("/tmp/r5.md")
    assert "/tmp/r5.md" not in settings.recent_documents()


def test_actor_kind_dimension_exists():
    assert "actor_kind" in TopDimension.__args__


def test_trailing_newline_rejected():
    """re.fullmatch prevents trailing newlines from being accepted."""
    assert not is_account_id("111111111111\n")
    assert not is_region("us-east-1\n")


def test_unicode_digit_rejected():
    """Region regex uses [0-9] not \\d to reject Unicode digits."""
    assert not is_region("us-east-١")  # Arabic-Indic digit


def test_empty_path_rejected(settings):
    """add_recent_document raises ValueError for empty path."""
    with pytest.raises(ValueError):
        settings.add_recent_document("")


def test_path_with_newline_rejected(settings):
    """add_recent_document raises ValueError for path containing \\n or \\r."""
    with pytest.raises(ValueError):
        settings.add_recent_document("path/with\nnewline")
    with pytest.raises(ValueError):
        settings.add_recent_document("path/with\rreturn")


def test_account_label_normalizes_color(settings):
    """account_label returns QColor.name() normalized form."""
    settings.set_account_label("111111111111", "test", "red")
    label = settings.account_label("111111111111")
    assert label is not None
    assert label[1] == "#ff0000"  # "red" normalized to hex


def test_account_regions_dedupes_first_order(settings):
    """set_account_regions dedupes while keeping first-seen order."""
    settings.set_account_regions("111111111111", ["us-east-1", "eu-west-1", "us-east-1"])
    assert settings.account_regions("111111111111") == ("us-east-1", "eu-west-1")
