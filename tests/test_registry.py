import sys

import pytest
from PySide6.QtCore import QSettings

from aws95.contracts.common import Result
from aws95.core.settings import Settings
from aws95.core.tasks import TaskRunner
from aws95.kit.widgets.stateview import ViewState
from aws95.providers.empty import empty_provider_set
from aws95.shell.context import NullSound, ProgramContext
from aws95.shell.registry import ProgramManifest, Registry
from tests.account_stubs import FakeSignal

PROGRAM_IDS = ["accounts", "resources", "billing", "findings", "activity",
                   "snapshots", "fleet", "insights", "reports"]


def _m(**kw):
    base = dict(id="x", title="X", icon="app", category="programs", desktop_icon=False,
                shortcut=None, instancing="singleton", factory="aws95.programs.display:make_display")
    base.update(kw)
    return ProgramManifest(**base)


def test_registry_validation():
    reg = Registry([_m(id="a", order=2), _m(id="b", order=1)])
    assert [m.id for m in reg.all()] == ["b", "a"]
    with pytest.raises(ValueError):
        reg.register(_m(id="a"))
    with pytest.raises(ValueError):
        reg.register(_m(id="c", icon="no-such-icon"))
    with pytest.raises(KeyError):
        reg.get("zzz")


def test_default_manifests_cover_every_program():
    from aws95.programs import DEFAULT_MANIFESTS
    reg = Registry(DEFAULT_MANIFESTS)
    ids = [m.id for m in reg.all()]
    for pid in PROGRAM_IDS:
        assert pid in ids
    assert all(m.desktop_icon for m in reg.desktop())
    assert {m.shortcut for m in reg.with_shortcuts()} >= {"Ctrl+Shift+R", "Ctrl+Shift+B", "Ctrl+Shift+L"}


def test_registry_import_is_lazy():
    import subprocess
    code = ("import sys, aws95.programs as p; from aws95.shell.registry import Registry; Registry(p.DEFAULT_MANIFESTS);"
            "print(sorted(m for m in sys.modules if m.startswith('aws95.programs.')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


class _FakeShell:
    def __init__(self):
        self.sign_in_succeeded = FakeSignal()

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture
def ctx(tmp_path, qapp):
    runner = TaskRunner()
    yield ProgramContext(Settings(QSettings(str(tmp_path / "c.ini"), QSettings.Format.IniFormat)),
                         runner, empty_provider_set(), NullSound(), _FakeShell())
    runner.shutdown()


@pytest.mark.parametrize("pid", PROGRAM_IDS)
def test_programs_open_not_connected(ctx, qtbot, pid):
    from aws95.kit.widgets.stateview import StateView
    from aws95.programs import DEFAULT_MANIFESTS
    widget = Registry(DEFAULT_MANIFESTS).get(pid).load_factory()(ctx)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: any(v.state is ViewState.NOT_CONNECTED for v in widget.findChildren(StateView)))


def test_null_sound():
    assert NullSound().play("startup") is False and NullSound().preview("click") is False


def test_duplicate_shortcut_rejected():
    reg = Registry([_m(id="a", shortcut="Ctrl+1")])
    with pytest.raises(ValueError, match="already used by 'a'"):
        reg.register(_m(id="b", shortcut="Ctrl+1"))
    reg.register(_m(id="c", shortcut=None))
    reg.register(_m(id="d", shortcut=None))  # None never collides


def test_default_manifests_have_unique_shortcuts():
    from aws95.programs import DEFAULT_MANIFESTS
    shortcuts = [m.shortcut for m in DEFAULT_MANIFESTS if m.shortcut]
    assert len(shortcuts) == len(set(shortcuts))
