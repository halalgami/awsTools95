"""Build the packaged app with Nuitka: `uv run python -m tools.build_nuitka` (current OS, output in dist/)."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

import aws95

Target = Literal["macos", "windows"]

ROOT = Path(__file__).resolve().parent.parent
PRODUCT_NAME = "AWS 95 Tools"
ENTRY = f"{PRODUCT_NAME}.py"   # Nuitka names the app after this file: "AWS 95 Tools.app" / "AWS 95 Tools.exe"
ICONS = "aws95/assets/icons"


def product_version() -> str:
    """aws95.__version__ without a .devN suffix: Nuitka needs a numeric 1-4 part version."""
    return re.sub(r"\.?(dev|a|b|rc)\d*$", "", aws95.__version__)


def build(target: Target, *, onefile: bool = False) -> list[str]:
    """The Nuitka command line for `target` (not run)."""
    if target not in ("macos", "windows"):
        raise ValueError(f"unsupported target: {target!r}")
    version = product_version()
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--include-qt-plugins=multimedia",   # QSoundEffect needs the ffmpeg backend plugin
        "--include-package=aws95",
        "--include-package-data=aws95",     # sounds, icons, demo fixtures
        # botocore service models (JSON) - without them every client fails in the packaged app
        "--include-package=botocore",
        "--include-package-data=botocore",
        "--include-package-data=boto3",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=tests",
        "--nofollow-import-to=tools",
        "--output-dir=dist",
        f"--product-name={PRODUCT_NAME}",
        f"--product-version={version}",
        "--assume-yes-for-downloads",
    ]
    if target == "macos":
        cmd += [
            "--macos-create-app-bundle",
            f"--macos-app-icon={ICONS}/aws95.icns",
            f"--macos-app-name={PRODUCT_NAME}",
            f"--macos-app-version={version}",
        ]
    else:
        cmd += [
            f"--windows-icon-from-ico={ICONS}/aws95.ico",
            "--windows-console-mode=disable",
        ]
    if onefile:
        cmd.append("--onefile")
    cmd.append(ENTRY)
    return cmd


def artifact(target: Target) -> str:
    """Where a standalone build of `target` leaves the app, relative to the repo root."""
    if target == "macos":
        return f"dist/{PRODUCT_NAME}.app"
    if target == "windows":
        return f"dist/{PRODUCT_NAME}.dist/{PRODUCT_NAME}.exe"
    raise ValueError(f"unsupported target: {target!r}")


def current_target() -> Target:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    raise SystemExit(f"no packaged build for platform {sys.platform!r} (macOS and Windows only)")


def main() -> int:
    import subprocess

    target = current_target()
    cmd = build(target)
    print(" ".join(f'"{a}"' if " " in a else a for a in cmd), flush=True)
    code = subprocess.call(cmd, cwd=ROOT)
    if code != 0:
        return code
    expected = artifact(target)
    if not (ROOT / expected).exists():
        print(f"Nuitka finished, but {expected} is missing: the app was not named after {ENTRY!r}.", flush=True)
        return 1
    print(f"Built {expected}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
