# Upstream: labsnap/tests/_org_scan.py @ 1b1d437 — adapted for aws95; owned here from now on.
"""Shared helper for asserting no organisation-specific content leaked into
the repo (see tests/test_docs.py and tests/test_builtin_rules.py).

A real account id or company name must never be a literal in a committed
test file - that is precisely the bug this module exists to avoid repeating
(a real customer account id was previously hardcoded as the "banned needle"
itself). So this module deliberately hardcodes none. Two independent checks
compose:

1. Configurable needles - comma-separated strings from the
   ``LABSNAP_ORG_NEEDLES`` env var, or newline-separated from a gitignored
   ``tests/org_needles.local.txt``. Empty by default: there is nothing
   organisation-specific to hardcode here, and CI/local operators can supply
   their own real needles (a company name, an internal codename) without
   ever committing them.
2. A structural check that needs no configuration: any 12-digit,
   account-id-shaped literal that is not a documented placeholder form (AWS's
   own documentation example, labsnap's own fixture-scrub marker, or an
   obviously-patterned dummy such as ``111111111111`` / ``111122223333`` /
   ``112233445566``) is flagged. This is what keeps the test meaningful even
   with zero configuration.
"""
import os
import pathlib
import re

ACCOUNT_ID_RE = re.compile(r"\b\d{12}\b")

# labsnap's own fixture-scrub marker (see labsnap/fixtures.py: ACCOUNT_MARKER).
# Duplicated as a literal rather than imported, so this scan has no
# dependency on labsnap's internals changing shape.
_SCRUB_MARKER_PREFIX = "000000"

# AWS's own documentation convention for a placeholder account id, used
# throughout AWS docs and examples (e.g. arn:aws:iam::123456789012:root).
_AWS_DOC_EXAMPLE_ACCOUNT = "123456789012"


def _is_placeholder_account_id(acct: str) -> bool:
    if acct == _AWS_DOC_EXAMPLE_ACCOUNT:
        return True
    if acct.startswith(_SCRUB_MARKER_PREFIX):
        return True
    if len(set(acct)) == 1:
        return True  # e.g. 111111111111
    if all(len(set(acct[i:i + 4])) == 1 for i in (0, 4, 8)):
        return True  # e.g. 111122223333
    if all(len(set(acct[i:i + 2])) == 1 for i in range(0, 12, 2)):
        return True  # e.g. 112233445566
    return False


def org_needles():
    env = os.environ.get("LABSNAP_ORG_NEEDLES")
    if env:
        return tuple(n.strip().lower() for n in env.split(",") if n.strip())
    local = pathlib.Path(__file__).parent / "org_needles.local.txt"
    if local.exists():
        return tuple(
            line.strip().lower() for line in local.read_text().splitlines()
            if line.strip())
    return ()


def find_offenders(paths, *, skip=()):
    """Return a list of "path: reason" strings for every file in `paths`
    that contains a configured needle or an unexplained account-id-shaped
    literal. `skip` excludes this module's own kind of file (e.g. the test
    file performing the scan) from being scanned."""
    needles = org_needles()
    skip = {pathlib.Path(p).resolve() for p in skip}
    offenders = []
    for f in paths:
        f = pathlib.Path(f)
        if not f.is_file() or f.resolve() in skip:
            continue
        text = f.read_text(errors="ignore")
        lower = text.lower()
        for needle in needles:
            if needle in lower:
                offenders.append(f"{f}: needle {needle!r}")
        for m in ACCOUNT_ID_RE.finditer(text):
            if not _is_placeholder_account_id(m.group(0)):
                offenders.append(f"{f}: account-id-shaped literal {m.group(0)}")
    return offenders
