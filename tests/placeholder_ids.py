"""Placeholder account ids and organisation-name needles shared by every leak scan in this repo.

The single source is tools/publish_check.py (2p). This module only re-exports it, so the 2b fixture scan
(tests/test_labsnap_fixture_scan.py) keeps its imports. Organisation names are stored only as SHA-256 digests of
lower-cased tokens, so neither file names them."""
from __future__ import annotations

from tools.publish_check import (
    ALLOWED_ACCOUNT_IDS,
    NEEDLES,
    SCRUB_MARKER_PREFIX,
    Needles,
    account_allowed,
    digest,
    org_name_hits,
)

__all__ = ["ALLOWED_ACCOUNT_IDS", "NEEDLES", "SCRUB_MARKER_PREFIX", "Needles", "account_allowed", "digest",
           "org_name_hits"]
