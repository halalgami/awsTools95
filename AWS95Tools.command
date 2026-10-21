#!/bin/bash
# AWS 95 Tools - macOS launcher. Double-click to run. Add --demo after the command to see watermarked demo data.
cd "$(dirname "$0")" || exit 1
if command -v uv >/dev/null 2>&1; then
    exec uv run python -m aws95 "$@"
else
    exec .venv/bin/python -m aws95 "$@"
fi
