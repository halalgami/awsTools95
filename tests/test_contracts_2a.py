import dataclasses
from datetime import datetime, timezone

import pytest

from aws95.contracts.accounts import CapabilityCheck, ConfigSource, Profile, RosterEntry
from aws95.contracts.activity import ActivityQuery
from aws95.contracts.common import Freshness, Result, Status
from aws95.contracts.resources import ResourceRow


def test_error_and_not_connected_carry_freshness():
    f = Freshness(datetime(2026, 10, 5, tzinfo=timezone.utc), "attempt")
    assert Result.error("x", freshness=f).freshness == f
    assert Result.not_connected("y", freshness=f).freshness == f
    assert Result.error("x").freshness is None


def test_profile_new_fields_default():
    p = Profile("dev", "profile", None)
    assert (p.selected, p.issue, p.verify, p.sign_in_profile) == (False, None, "auto", None)


def test_new_contracts_frozen():
    c = CapabilityCheck("identity", "Identity", Status.OK, (), "ok", False, True)
    with pytest.raises(Exception):
        c.status = Status.DENIED  # type: ignore[misc]
    assert ConfigSource("/x", "config", False, 0).exists is False
    assert RosterEntry("dev", "123456789012").account_id == "123456789012"


@pytest.mark.parametrize("bad", [-1, 4])
def test_risk_min_bounded_to_risk_levels(bad):
    with pytest.raises(ValueError):
        ActivityQuery(risk_min=bad)


@pytest.mark.parametrize("ok", [0, 3])
def test_risk_min_edges(ok):
    ActivityQuery(risk_min=ok)


def test_mapping_field_contracts_are_unhashable():
    """Triage #7: rows carrying Mapping fields must never be used in sets or as dict keys."""
    fields = {f.name: f for f in dataclasses.fields(ResourceRow)}
    assert any("Mapping" in str(f.type) or "dict" in str(f.type) for f in fields.values())
    row = ResourceRow(account="111111111111", rtype="ec2.instance", region="us-east-1",
                      rid="i-1", name="web", state="running")
    with pytest.raises(TypeError):
        hash(row)
