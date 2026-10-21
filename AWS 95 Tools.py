"""Packaged-app entry point. Nuitka names the app after this file: "AWS 95 Tools.app" (macOS), "AWS 95 Tools.exe" (Windows)."""
from aws95.app import main

if __name__ == "__main__":
    raise SystemExit(main())
