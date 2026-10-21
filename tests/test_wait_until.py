import pytest
from pytestqt.exceptions import TimeoutError as QtTimeoutError
from pytestqt.qtbot import QtBot


def test_wait_until_returns_on_true_none_and_times_out_with_the_same_errors(qtbot):
    qtbot.waitUntil(lambda: True)
    qtbot.waitUntil(lambda: None)
    calls = []

    def third_time():
        calls.append(1)
        assert len(calls) >= 3

    qtbot.waitUntil(third_time)
    assert len(calls) == 3
    with pytest.raises(QtTimeoutError, match="waitUntil timed out in 30 milliseconds"):
        qtbot.waitUntil(lambda: False, timeout=30)
    with pytest.raises(QtTimeoutError):
        qtbot.wait_until(lambda: (_ for _ in ()).throw(AssertionError("no")), timeout=30)
    with pytest.raises(ValueError, match="must return None, True or False"):
        qtbot.waitUntil(lambda: [])


def test_override_is_installed_and_alias_uses_it(qtbot, monkeypatch):
    assert hasattr(QtBot.waitUntil, "__wrapped__")
    assert qtbot.wait_until.__func__ is QtBot.waitUntil
    waits = []
    real = QtBot.wait
    monkeypatch.setattr(QtBot, "wait", lambda self, ms: (waits.append(ms), real(self, ms))[1])
    flag = []
    qtbot.wait_until(lambda: flag.append(1) or len(flag) > 2)
    assert waits == [1, 1]
    assert "wait" not in vars(qtbot)
