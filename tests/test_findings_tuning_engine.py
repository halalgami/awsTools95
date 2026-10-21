from datetime import datetime, timezone

import pytest

from aws95.aws.engines.labsnap.analyze.findings import open_findings, recheck, resolved_findings
from aws95.aws.engines.labsnap.rules.base import RuleOverrides, load_rules, validate_params
from aws95.core.cancel import CancelToken
from tests.store_seed import DEV, PINNED, seed_store

T = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = seed_store(tmp_path / "aws95.db")
    yield s
    s.close()


def latest(store, account=DEV):
    return store.latest_snapshot(account)["id"]


def rules_open(store):
    return {(f.rule, f.rid) for f in open_findings(store, (DEV,), now=PINNED)}


def test_overrides_from_store(store):
    store.set_rule_override("s3.no_lifecycle", disabled=True, severity=None, params=None, changed_at=T)
    store.set_rule_override("ec2.sg_open_admin_port", disabled=False, severity="critical", params={"ports": [22]},
                            changed_at=T)
    o = RuleOverrides.from_store(store)
    rules = {r.id: r for r in load_rules(o)}
    assert "s3.no_lifecycle" not in rules
    assert rules["ec2.sg_open_admin_port"].severity == "critical" and rules["ec2.sg_open_admin_port"].params == {"ports": [22]}


def test_validate_params():
    """Review Focus 2."""
    assert validate_params("ec2.sg_open_admin_port", {"ports": [22, 443]}) == {"ports": [22, 443]}
    assert validate_params("iam.key_stale", {"max_age_days": 90}) == {"max_age_days": 90}
    for bad, word in (({"ports": "22"}, "ports"), ({"ports": [22, "x"]}, "ports"), ({"nope": 1}, "nope")):
        with pytest.raises(ValueError, match=word):
            validate_params("ec2.sg_open_admin_port", bad)
    with pytest.raises(ValueError, match="max_age_days"):
        validate_params("iam.key_stale", {"max_age_days": True})
    with pytest.raises(ValueError, match="Unknown rule"):
        validate_params("no.such.rule", {})


def test_disabled_rule_is_hidden_not_resolved(store):
    """Review Focus 1."""
    assert ("s3.no_lifecycle", "example-logs") in rules_open(store)
    store.set_rule_override("s3.no_lifecycle", disabled=True, severity=None, params=None, changed_at=T)
    assert recheck(store, [latest(store)]) == 1
    assert not [k for k in rules_open(store) if k[0] == "s3.no_lifecycle"]
    assert not [f for f in resolved_findings(store, (DEV,), now=PINNED) if f.rule == "s3.no_lifecycle"]
    store.clear_rule_override("s3.no_lifecycle")
    recheck(store, [latest(store)])
    assert ("s3.no_lifecycle", "example-logs") in rules_open(store)


def test_recheck_applies_severity(store):
    store.set_rule_override("iam.user_no_mfa", disabled=False, severity="low", params=None, changed_at=T)
    recheck(store, [latest(store)])
    [f] = [f for f in open_findings(store, (DEV,), now=PINNED) if f.rule == "iam.user_no_mfa"]
    assert f.severity == "low"


def test_recheck_cancel_returns_what_finished(store):
    with store.reader() as db:
        ids = [r["id"] for r in db.execute("SELECT id FROM snapshots ORDER BY id")]
    token = CancelToken()
    seen = []

    def progress(done, total):
        seen.append((done, total))
        if done == 1:
            token.cancel()
    assert recheck(store, ids, cancel=token, progress=progress) == 1 and seen == [(1, len(ids))]


def test_new_snapshots_use_the_stored_tuning(store, monkeypatch):
    from aws95.aws.engines.labsnap import service as service_mod
    store.set_rule_override("s3.no_lifecycle", disabled=True, severity=None, params=None, changed_at=T)
    svc = service_mod.SnapshotService(store)
    assert "s3.no_lifecycle" not in {r.id for r in svc._rules()}
