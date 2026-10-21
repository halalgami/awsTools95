"""Full-desktop screenshots for the README (docs/screenshots/), from the synthetic sample store only.

Runs offscreen under a temporary HOME with placeholder accounts (the test fixtures `dev` 123456789012 and `prod`
210987654321) and a pinned clock, so no real profile, account or data can appear in a public image. Never calls AWS.

    uv run python -m tools.make_readme_screenshots
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
SIZE = (1440, 900)


def _settle(app, seconds: float = 1.5) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="aws95-shots-"))
    (home / ".aws").mkdir()
    config = home / ".aws" / "config"
    config.write_text("[profile dev]\nregion = eu-west-1\n\n[profile prod]\nregion = us-east-1\n")
    os.environ.update({"HOME": str(home), "AWS_CONFIG_FILE": str(config), "QT_QPA_PLATFORM": "offscreen",
                       "AWS_SHARED_CREDENTIALS_FILE": str(home / ".aws" / "credentials")})
    sys.path.insert(0, str(ROOT))

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    app = QApplication([])

    from aws95.core.settings import Settings
    from aws95.core.tasks import TaskRunner
    from aws95.kit import theme as th
    from aws95.kit.style import apply_theme
    from aws95.kit.widgets import statusstrip
    from aws95.programs import DEFAULT_MANIFESTS
    from aws95.providers.live import live_provider_set
    from aws95.shell.context import NullSound
    from aws95.shell.registry import Registry
    from aws95.shell.shell import ShellWindow
    from tests.activity_seed import seed_activity
    from tests.store_seed import DEV, PINNED, PROD, ROSTER, seed_store

    from aws95.programs.activity import ActivityProgram
    statusstrip._utcnow = lambda: PINNED
    ActivityProgram.clock = staticmethod(lambda: PINNED)       # "last 7 days" means the sample data's week
    OUT.mkdir(parents=True, exist_ok=True)
    # (name, theme, ((program, x, y, width, height), ...)): overlapping windows, so the desktop and icons show.
    shots = (("desktop-light", th.LIGHT, (("billing", 130, 24, 780, 600), ("findings", 520, 250, 860, 540))),
             ("desktop-dark", th.DARK, (("activity", 130, 24, 820, 620), ("snapshots", 600, 230, 800, 560))))
    for name, theme, programs in shots:
        settings = Settings(QSettings(str(home / f"{name}.ini"), QSettings.Format.IniFormat))
        settings.set_roster(ROSTER)
        settings.set_account_label(DEV, "dev", "#2e8b57")
        settings.set_account_label(PROD, "prod", "#b22222")
        store = seed_store(home / f"{name}.db")
        seed_activity(store)
        runner = TaskRunner()
        providers = live_provider_set(settings, store=store, clock=lambda: PINNED)
        apply_theme(app, theme)
        win = ShellWindow(settings, runner, providers, NullSound(), Registry(DEFAULT_MANIFESTS))
        win.resize(*SIZE)
        win.show()
        _settle(app, 0.5)
        for pid, x, y, w, h in programs:
            window = win.open_program(pid)
            window.setGeometry(x, y, w, h)
            if pid == "snapshots":
                window.content().tabs.setCurrentWidget(window.content().run_tab)
            _settle(app, 0.8)
        _settle(app)
        path = OUT / f"{name}.png"
        win.grab().save(str(path))
        print(f"wrote {path.relative_to(ROOT)}")
        win.close()
        runner.shutdown()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
