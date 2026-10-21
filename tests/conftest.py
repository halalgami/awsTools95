import os

# Must be set before any Qt import so tests run headless on macOS and Windows.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


import pytest  # noqa: E402


def _make_fast_wait_until(original):
    """Wrap pytest-qt's own waitUntil so its poll wait(10) becomes wait(1); nothing else about it changes."""

    def fast(self, callback, **kwargs):
        __tracebackhide__ = True
        real_wait = self.wait
        self.wait = lambda ms: real_wait(1 if ms == 10 else ms)
        try:
            return original(self, callback, **kwargs)
        finally:
            del self.wait

    fast.__wrapped__ = original
    return fast


@pytest.fixture(autouse=True, scope="session")
def _auto_mnemonics():
    """Alt mnemonics behave as in the app (aws95.app.main turns them on for every platform, ruling M3-R22)."""
    from PySide6.QtGui import qt_set_sequence_auto_mnemonic

    qt_set_sequence_auto_mnemonic(True)
    yield


@pytest.fixture(autouse=True, scope="session")
def _fast_wait_until():
    from pytestqt.qtbot import QtBot

    saved = QtBot.waitUntil
    QtBot.waitUntil = _make_fast_wait_until(saved)
    yield
    QtBot.waitUntil = saved


@pytest.fixture(autouse=True)
def _reset_theme_global():
    from aws95.kit import theme
    theme.set_current(theme.LIGHT)
    yield
    theme.set_current(theme.LIGHT)


@pytest.fixture(autouse=True)
def _reset_mascot_global(monkeypatch):
    """The empty-state mascot is a process-wide global that app startup sets; start every test without it
    (the screenshot fixture pins its own artwork on top of this)."""
    from aws95.kit.widgets import stateview
    monkeypatch.setattr(stateview, "_mascot_pixmap", None)


@pytest.fixture(autouse=True)
def _cleanup_toplevel_widgets():
    """Close and delete all top-level widgets after each test to avoid repolishing on next test's theme reset.

    Owned popups (menu-bar menus, toolbar overflow menus, combo popups) are left to their owners:
    deleting one directly leaves its owner with a dangling pointer and segfaults later (segv-report.md).
    """
    yield
    # Import Qt modules lazily so QT_QPA_PLATFORM is set before any Qt import
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from aws95.app import close_top_level_windows

    app = QApplication.instance()
    if app is not None:
        try:
            # Process any pending events first to ensure clean state
            app.processEvents()
            close_top_level_windows()
            app.processEvents()
            # processEvents() outside a running loop never honours deleteLater(): without this flush every
            # test's windows lived on until some later test spun a nested loop, and each theme switch
            # repolished them all.
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        except RuntimeError:
            # Only "Internal C++ object already deleted" is expected here; anything else must fail the test.
            pass


@pytest.fixture(autouse=True)
def _end_screensaver_sessions():
    """A saver session survives closing its windows (ruling R9), so end any a test left running.
    Declared after the top-level cleanup, so it tears down first and the cleanup closes what it re-shows."""
    yield
    import sys

    manager = sys.modules.get("aws95.shell.screensaver.manager")
    if manager is None:
        return
    from shiboken6 import isValid

    for m in list(manager.live_sessions):
        if isValid(m):
            m.dismiss()


from tests.account_stubs import FakeSignal  # noqa: E402


class RecordingShell:
    """Stands in for ShellServices in program tests: records every call, returns None."""

    def __init__(self):
        self.calls = []
        self.sign_in_succeeded = FakeSignal()
        self.snapshot_finished = FakeSignal()
        self.snapshot_progress = FakeSignal()
        self.activity_finished = FakeSignal()
        self.activity_progress = FakeSignal()

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args))
        return None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        from functools import partial
        return partial(self._record, name)


@pytest.fixture
def program_env(tmp_path, qapp):
    from PySide6.QtCore import QSettings

    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.providers.empty import empty_provider_set
    from aws95.shell.context import NullSound, ProgramContext

    runners = []

    def make(factory, providers=None):
        settings = Settings(QSettings(str(tmp_path / "program.ini"), QSettings.Format.IniFormat))
        runner = TaskRunner()
        runners.append(runner)
        ctx = ProgramContext(settings, runner, providers or empty_provider_set(), NullSound(), RecordingShell())
        return factory(ctx), ctx

    yield make
    for runner in runners:
        runner.shutdown()


def pytest_addoption(parser):
    parser.addoption("--update-screenshots", action="store_true", default=False,
                     help="write screenshot goldens instead of comparing")


@pytest.fixture(autouse=True)
def _hermetic_aws(monkeypatch, tmp_path_factory):
    """Spec §9: no test sees the developer's AWS config, credentials, profile or instance metadata,
    and booting the app in a test never goes live."""
    import os
    for key in list(os.environ):
        if key.startswith("AWS_"):
            monkeypatch.delenv(key, raising=False)
    empty = tmp_path_factory.mktemp("no-aws")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(empty / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(empty / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS95_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _hermetic_home(monkeypatch, tmp_path_factory):
    """No test reads or writes the real app directory (aws95.db, exports): AWS95_HOME is a fresh temp dir."""
    monkeypatch.setenv("AWS95_HOME", str(tmp_path_factory.mktemp("aws95-home")))


@pytest.fixture
def make_store(tmp_path):
    """Stores made through this factory are closed at teardown. New tests use it (or close their Store)."""
    from aws95.store import Store
    made = []

    def make(name: str = "aws95.db"):
        store = Store(tmp_path / name)
        made.append(store)
        return store
    yield make
    for store in made:
        store.close()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Spec §9: record and refuse every INET connect / DNS lookup; fail the test if any happened
    (botocore retries and wraps socket errors, so raising alone could pass silently)."""
    import socket
    attempts = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(("connect", address))
            raise OSError("network access blocked in tests")
        return real_connect(self, address)

    def getaddrinfo(host, *args, **kwargs):
        attempts.append(("dns", host))
        raise socket.gaierror("DNS blocked in tests")

    def create_connection(address, *args, **kwargs):
        attempts.append(("create_connection", address))
        raise OSError("network access blocked in tests")

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(("connect_ex", address))
            return 111
        return real_connect_ex(self, address)

    def gethostbyname(host):
        attempts.append(("dns", host))
        raise socket.gaierror("DNS blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "gethostbyname", gethostbyname)
    monkeypatch.setattr(socket, "gethostbyname_ex", gethostbyname)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    yield
    assert attempts == [], f"test attempted network access: {attempts}"
