"""Packaging: assets resolve from the package, Nuitka command lines, and the boot-timing hook."""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import aws95
import aws95.app as app_module
from aws95.core.settings import Settings
from tools import build_nuitka

REPO = Path(__file__).resolve().parent.parent


def test_assets_resolve_from_package(tmp_path):
    code = (
        "from aws95.shell.sound import SOUND_DIR; from aws95.app import ICON_DIR; "
        "from aws95.providers.demo.store import FIXTURE_DIR; "
        "assert any(SOUND_DIR.glob('*.wav')), SOUND_DIR; "
        "assert (ICON_DIR / 'aws95.ico').is_file() and list(ICON_DIR.glob('aws95-*.png')), ICON_DIR; "
        "assert (ICON_DIR / 'aws95.icns').is_file(); "
        "assert (FIXTURE_DIR / 'accounts.json').is_file(), FIXTURE_DIR; "
        "import aws95; "
        "[__import__('sys').exit(p) for p in (SOUND_DIR, ICON_DIR, FIXTURE_DIR) if not str(p).startswith(str(__import__('pathlib').Path(aws95.__file__).resolve().parent))]"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                          env={"PYTHONPATH": str(REPO), "PATH": "", "QT_QPA_PLATFORM": "offscreen"})
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("target", ["macos", "windows"])
def test_common_flags(target):
    cmd = build_nuitka.build(target)
    assert cmd[:3] == [sys.executable, "-m", "nuitka"]
    for flag in ("--standalone", "--enable-plugin=pyside6", "--include-qt-plugins=multimedia", "--include-package-data=aws95",
                 "--output-dir=dist", "--product-name=AWS 95 Tools",
                 f"--product-version={aws95.__version__.split('.dev')[0]}",
                 "--nofollow-import-to=pytest", "--nofollow-import-to=tests", "--nofollow-import-to=tools",
                 "--product-version=3.1.0"):
        assert flag in cmd, flag
    assert "--onefile" not in cmd
    assert cmd[-1] == "AWS 95 Tools.py"
    assert "--include-package=aws95" in cmd
    assert "--mode=standalone" not in cmd


@pytest.mark.parametrize("target", ["macos", "windows"])
def test_botocore_data_included(target):
    cmd = build_nuitka.build(target)
    for flag in ("--include-package-data=botocore", "--include-package-data=boto3", "--include-package=botocore"):
        assert flag in cmd, flag


def test_macos_flags():
    cmd = build_nuitka.build("macos")
    assert "--macos-create-app-bundle" in cmd
    assert "--macos-app-icon=aws95/assets/icons/aws95.icns" in cmd
    assert "--macos-app-name=AWS 95 Tools" in cmd
    assert not any(a.startswith("--windows-") for a in cmd)


def test_windows_flags():
    cmd = build_nuitka.build("windows")
    assert "--windows-icon-from-ico=aws95/assets/icons/aws95.ico" in cmd
    assert "--windows-console-mode=disable" in cmd
    assert not any(a.startswith("--macos-") for a in cmd)


def test_onefile_flag():
    assert "--onefile" in build_nuitka.build("windows", onefile=True)


def test_unknown_target_rejected():
    with pytest.raises(ValueError):
        build_nuitka.build("linux")  # type: ignore[arg-type]


# -- AWS95_EXIT_AFTER_BOOT_MS -------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [(None, None), ("", None), ("abc", None), ("-5", None), ("0", 0), ("250", 250),
                                          ("\u00b2", None), ("\u0663", None)])
def test_exit_after_boot_ms_parsing(raw, expected):
    env = {} if raw is None else {"AWS95_EXIT_AFTER_BOOT_MS": raw}
    assert app_module.exit_after_boot_ms(env) == expected


@pytest.fixture
def tmp_settings(monkeypatch, tmp_path):
    ini = str(tmp_path / "p.ini")

    class TmpSettings(Settings):
        def __init__(self, qsettings=None, parent=None):
            super().__init__(QSettings(ini, QSettings.Format.IniFormat), parent)

    monkeypatch.setattr("aws95.core.settings.Settings", TmpSettings)
    monkeypatch.delenv("AWS95_DEMO", raising=False)


def test_exit_after_boot_quits_with_zero_and_prints_elapsed(qapp, tmp_settings, monkeypatch, capsys):
    monkeypatch.setenv("AWS95_EXIT_AFTER_BOOT_MS", "0")
    assert app_module.main(["--demo"]) == 0      # a real event loop: it must end by itself
    out = capsys.readouterr().out
    assert out.startswith("AWS95_BOOT_MS=")
    assert float(out.split("=")[1].split()[0]) > 0


def test_unset_env_does_not_quit_or_print(qapp, tmp_settings, monkeypatch, capsys):
    monkeypatch.delenv("AWS95_EXIT_AFTER_BOOT_MS", raising=False)
    monkeypatch.setattr(QApplication, "exec", lambda *a, **k: 0)
    assert app_module.main([]) == 0
    assert "AWS95_BOOT_MS" not in capsys.readouterr().out


def test_boot_ms_print_skipped_without_stdout(monkeypatch):
    """A Windows windowed build has sys.stdout None: the boot-ms print must not crash."""
    import sys

    class FakeApp:
        def processEvents(self):
            pass

    scheduled = []
    monkeypatch.setattr(app_module, "_boot_t0", 0.0)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr("PySide6.QtCore.QTimer.singleShot", lambda ms, fn: scheduled.append(ms))
    app_module._quit_after_first_paint(FakeApp(), 5)
    assert scheduled == [5]


# -- packaged-app name (spec §6.2) ---------------------------------------------------------------------

def test_entry_module_names_the_app():
    assert build_nuitka.ENTRY == "AWS 95 Tools.py"
    assert Path(build_nuitka.ENTRY).stem == build_nuitka.PRODUCT_NAME == "AWS 95 Tools"
    assert (REPO / build_nuitka.ENTRY).is_file()


def test_entry_module_runs_the_app(tmp_path):
    proc = subprocess.run([sys.executable, str(REPO / build_nuitka.ENTRY), "--version"], cwd=tmp_path,
                          capture_output=True, text=True,
                          env={"PYTHONPATH": str(REPO), "PATH": "", "QT_QPA_PLATFORM": "offscreen"})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"AWS 95 Tools {aws95.__version__}"


@pytest.mark.parametrize("target,expected", [("macos", "dist/AWS 95 Tools.app"),
                                             ("windows", "dist/AWS 95 Tools.dist/AWS 95 Tools.exe")])
def test_artifact_paths(target, expected):
    assert build_nuitka.artifact(target) == expected


def test_artifact_unknown_target_rejected():
    with pytest.raises(ValueError):
        build_nuitka.artifact("linux")  # type: ignore[arg-type]


@pytest.mark.parametrize("made,expected", [(True, 0), (False, 1)])
def test_main_checks_the_artifact(made, expected, tmp_path, monkeypatch, capsys):
    calls = []

    def fake_call(cmd, cwd=None):
        calls.append((cmd, cwd))
        if made:
            (tmp_path / "dist" / "AWS 95 Tools.app").mkdir(parents=True)
        return 0

    monkeypatch.setattr(build_nuitka, "ROOT", tmp_path)
    monkeypatch.setattr(build_nuitka, "current_target", lambda: "macos")
    monkeypatch.setattr(subprocess, "call", fake_call)
    assert build_nuitka.main() == expected
    [(cmd, cwd)] = calls
    assert cwd == tmp_path and cmd[-1] == "AWS 95 Tools.py"
    assert "dist/AWS 95 Tools.app" in capsys.readouterr().out


def test_main_returns_nuitka_failure_code(tmp_path, monkeypatch):
    monkeypatch.setattr(build_nuitka, "ROOT", tmp_path)
    monkeypatch.setattr(build_nuitka, "current_target", lambda: "windows")
    monkeypatch.setattr(subprocess, "call", lambda cmd, cwd=None: 3)
    assert build_nuitka.main() == 3


def test_source_launcher_version_matches():
    """The in-repo AWS95Tools.app source launcher (ruling M4-R1) carries the same version."""
    with (REPO / "AWS95Tools.app" / "Contents" / "Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    assert info["CFBundleShortVersionString"] == aws95.__version__
