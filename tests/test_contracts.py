import ast
import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aws95.contracts.activity import ActivityEvent, ActivityQuery
from aws95.contracts.billing import CostPoint, CostSummary
from aws95.contracts.common import Coverage, Page, Result, Scope, Status, TimeRange
from aws95.contracts.findings import FleetCell
from aws95.contracts.resources import ResourceRow

UTC = timezone.utc


def test_result_helpers():
    ok = Result.ok([1], coverage=Coverage(("us-east-1",), ("us-east-1",)))
    assert ok.status is Status.OK and ok.data == [1] and not ok.is_demo
    nc = Result.not_connected("Billing data arrives in phase 2.")
    assert nc.status is Status.NOT_CONNECTED and nc.data is None
    assert Result.error("boom").status is Status.ERROR


def test_contracts_are_frozen():
    row = ResourceRow(account="111111111111", rtype="ec2.instance", region="us-east-1",
                      rid="i-1", name="web", state="running")
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.name = "x"


def test_money_must_be_decimal():
    with pytest.raises(TypeError):
        CostPoint(period="2026-09", amount=12.5, unit="USD")
    with pytest.raises(TypeError):
        CostSummary(mtd=Decimal("1"), forecast=3.0, last_month=None, currency="USD",
                    as_of=datetime(2026, 10, 2, tzinfo=UTC))
    assert CostPoint(period="2026-09", amount=Decimal("12.50"), unit="USD").amount == Decimal("12.50")


def test_coverage_completeness():
    assert Coverage(("a", "b"), ("a", "b")).complete
    assert not Coverage(("a", "b"), ("a",)).complete
    assert not Coverage(("a",), ("a",), denials=2).complete


def test_time_range_validation():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        TimeRange(start=start, end=datetime(2025, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        TimeRange(start=datetime(2026, 1, 1), end=datetime(2026, 1, 2))
    assert Scope().accounts == ()


def test_risk_level_bounds():
    kwargs = dict(event_id="e", account="111111111111", time_utc=datetime(2026, 1, 1, tzinfo=UTC),
                  principal_type="IAMUser", principal_name="alice", is_human=True,
                  source="iam.amazonaws.com", name="CreateUser", read_only=False, source_ip=None,
                  user_agent=None, resource_arns=(), region="us-east-1", risk_rule=None)
    with pytest.raises(ValueError):
        ActivityEvent(risk_level=4, **kwargs)
    assert ActivityEvent(risk_level=3, **kwargs).risk_level == 3
    assert ActivityQuery().risk_min == 0


def test_fleet_cell_symbol():
    assert FleetCell("r", "a", open_count=0, collected=True).symbol == "✓"
    assert FleetCell("r", "a", open_count=3, collected=True).symbol == "3"
    assert FleetCell("r", "a", open_count=1, collected=True).symbol == "✗"
    assert FleetCell("r", "a", open_count=0, collected=False).symbol == "?"


def test_page_defaults():
    page = Page(items=(1, 2))
    assert page.next_cursor is None and page.total is None


def test_contracts_import_no_qt_or_boto3():
    root = Path(__file__).resolve().parents[1] / "aws95" / "contracts"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(("PySide6", "boto3", "botocore")), f"{path.name} imports {name}"


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_money_must_be_finite(bad):
    with pytest.raises(ValueError):
        CostPoint(period="2026-09", amount=bad, unit="USD")


def test_cost_notes_are_the_spec_wording():
    """Spec §2.1: one source for every cost note the UI and README render."""
    from aws95.contracts import billing
    assert billing.BILLING_COST_NOTE == "≈ $0.06 per account (AWS Cost Explorer charges)"
    assert billing.BILLING_CHECK_NOTE == "AWS charges $0.01 per check"
    assert billing.IMPORT_COST_NOTE == ("Reads your CloudTrail log files from S3. AWS bills S3 requests and data transfer "
                                        "to your account (usually cents for a few days of logs; larger ranges cost more)")
