#!/bin/bash
# AWS 95 Tools - run from source with uv. Usage: ./run.sh [--demo] [--gallery]
set -e
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi
uv sync --quiet
exec uv run python -m aws95 "$@"
