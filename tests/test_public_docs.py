"""README, LICENSE and SECURITY: generated blocks current, claims backed by code, links resolve in the public tree."""
from __future__ import annotations

import json
import re
from pathlib import Path

import aws95
from tools import make_readme_policy, publish_check

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)|<img[^>]*\bsrc=\"([^\"]+)\"")


def _local_targets(text: str) -> list[str]:
    targets = [a or b for a, b in LINK.findall(text)]
    return [t.split("#")[0] for t in targets if not t.startswith(("http://", "https://", "#", "mailto:"))]


def test_readme_policy_is_current():
    assert make_readme_policy.main(["--check"]) == 0, "run: uv run python -m tools.make_readme_policy"
    for name in make_readme_policy.BLOCK_NAMES:
        assert README.count(make_readme_policy.begin(name)) == 1, name


def test_readme_cost_sentence_matches_billing_constants():
    """Review I8 / ruling D1: the cost sentence comes from 2b's constants, never typed by hand."""
    from aws95.aws.engines.labsnap.collect.collectors.cost import BILLED_CALLS
    from aws95.contracts.billing import BILLING_COST_NOTE
    assert make_readme_policy.billing_note(BILLED_CALLS, BILLING_COST_NOTE) in README
    assert BILLED_CALLS == 6 and "$0.06" in README and "$0.04" not in README      # billing-complete spec §2


REDACTION_CLAIM = "are redacted before anything is stored"
CANARY = "canary-7f3a9c"


def _redacted(out, original) -> str:
    return json.dumps(original if out is None else out)


def test_redaction_claim_is_backed():
    """Review I8 / C1: the README's redaction sentence holds only while 2b's and 2c's redactors drop the secrets."""
    assert REDACTION_CLAIM in README
    from aws95.aws.engines.labsnap.collect import redact
    fn = {"Functions": [{"FunctionName": "f", "Environment": {"Variables": {"API_KEY": CANARY}}}]}
    pool = {"UserPoolClient": {"ClientId": "c", "ClientSecret": CANARY}}
    td = {"taskDefinition": {"containerDefinitions": [{
        "environment": [{"name": "DB_PASSWORD", "value": CANARY}],
        "secrets": [{"name": "TOKEN", "valueFrom": f"arn:aws:ssm:us-east-1:123456789012:parameter/{CANARY}"}]}]}}
    for func, payload in ((redact.lambda_functions, fn), (redact.user_pool_client, pool), (redact.task_definition, td)):
        assert CANARY not in _redacted(func(payload), payload), func.__name__

    from aws95.aws.engines.ctwatch import redact as ct_redact      # 2c review C1
    record = {"eventName": "AssumeRole",
              "responseElements": {"credentials": {"sessionToken": CANARY, "expiration": "2026-10-07T00:00:00Z"}},
              "requestParameters": {"containerDefinitions": [{"environment": [{"name": "X", "value": CANARY}]}]}}
    assert CANARY not in _redacted(ct_redact.redact(record), record)


def test_readme_windows_match_shipped_parts():
    """Review Minor 10: what the README calls live or "later" matches the wizard's SHIPPED_PARTS."""
    from aws95.aws.permissions import CAPABILITIES, SHIPPED_PARTS
    assert {"2a", "2b", "2c"} <= SHIPPED_PARTS
    for cap in CAPABILITIES:
        if cap.available_in == "2a":
            continue
        if cap.shipped:
            assert f"**{cap.feature}**" in README, cap.feature
        else:
            assert f"**{cap.label}**" in README and "Arrive in a later update" in README, cap.label


def test_readme_links_resolve_in_public_tree():
    targets = _local_targets(README) + _local_targets((REPO / "SECURITY.md").read_text(encoding="utf-8"))
    assert targets, "README has no local links or images"
    for target in targets:
        assert (REPO / target).exists(), f"missing: {target}"
        assert not target.startswith(publish_check.EXCLUDED_DIRS), f"not in the public tree: {target}"


def test_readme_shows_screenshots_from_the_goldens():
    shots = [t for t in _local_targets(README) if t.startswith("tests/screenshots/")]
    assert len(shots) >= 4 and all(t.endswith(".png") for t in shots)


def test_readme_states_the_promises():
    for phrase in (f"Version {aws95.__version__}", "read-only", "uv sync", "uv run python -m aws95",
                   "--demo", "uv run python -m tools.build_nuitka", "dist/AWS 95 Tools.app",
                   "Cost Explorer", "$0.01", "Import History…", "AWS is the only network destination", "LICENSE",
                   "SECURITY.md"):
        assert phrase in README, phrase
    for stale in ("Legacy code", "phase 1 (UI) complete", "Athena"):
        assert stale not in README, stale
    # Spec §2.1: a "What costs money" section rendered from the same constants the UI shows.
    from aws95.contracts.billing import BILLING_CHECK_NOTE, BILLING_COST_NOTE, IMPORT_COST_NOTE
    assert "## What costs money" in README
    section = README.split("## What costs money", 1)[1].split("\n## ", 1)[0]
    for note in (BILLING_COST_NOTE, BILLING_CHECK_NOTE, IMPORT_COST_NOTE):
        assert note in section, note


def test_public_docs_have_no_leaks():
    for name in ("README.md", "LICENSE", "SECURITY.md"):
        assert publish_check.scan_text((REPO / name).read_text(encoding="utf-8"), name) == []


def test_license_is_mit():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n\nCopyright (c) 2026 Algam The Dev\n")
    assert 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND' in text


def test_security_policy_points_to_private_advisories():
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    assert "https://github.com/halalgami/awsTools95/security/advisories/new" in text
    assert "private" in text.lower() and "public issue" in text.lower()
