# Upstream: labsnap/tests/test_builtin_rules.py @ 1b1d437 — adapted for aws95; owned here from now on.
import aws95.aws.engines.labsnap.rules.builtin  # noqa: F401 - registers built-ins
from aws95.aws.engines.labsnap.rules.base import RuleOverrides
from aws95.aws.engines.labsnap.model import Resource
from aws95.aws.engines.labsnap.rules.base import load_rules
from tests.labsnap_org_scan import find_offenders


class FakeView:
    def __init__(self, resources=(), metrics=(), account="111111111111"):
        self._r = list(resources)
        self._m = list(metrics)
        self.account = account
        self.snapshot_id = 1
        self.taken_at = "2026-09-07T00:00:00Z"

    def resources(self, rtype=None):
        return [r for r in self._r if rtype is None or r.rtype == rtype]

    def metrics(self, name=None):
        return [m for m in self._m if name is None or m.name == name]


def run(rule_id, view):
    rule = [r for r in load_rules(RuleOverrides()) if r.id == rule_id][0]
    return list(rule.evaluate(view, rule.params))


def get_rule(rule_id):
    return [r for r in load_rules(RuleOverrides()) if r.id == rule_id][0]


# ---------------------------------------------------------------------------
# rds.py
# ---------------------------------------------------------------------------

def test_rds_public_flags_only_public_instances():
    v = FakeView([
        Resource("rds.instance", "us-east-1", "pub", {"publicly_accessible": True}),
        Resource("rds.instance", "us-east-1", "priv", {"publicly_accessible": False})])
    assert [f.rid for f in run("rds.public", v)] == ["pub"]


def test_rds_public_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("rds.instance", "us-east-1", "pub",
                           {"publicly_accessible": True}, partial=True)])
    assert [f.rid for f in run("rds.public", v)] == ["pub"]


def test_rds_public_ignores_missing_attribute():
    v = FakeView([Resource("rds.instance", "us-east-1", "unknown", {})])
    assert run("rds.public", v) == []


def test_rds_sg_open_flags_only_open_to_world():
    v = FakeView([
        Resource("rds.instance", "us-east-1", "open", {"open_to_world": True}),
        Resource("rds.instance", "us-east-1", "shut", {"open_to_world": False})])
    assert [f.rid for f in run("rds.sg_open", v)] == ["open"]


def test_rds_sg_open_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("rds.instance", "us-east-1", "open",
                           {"open_to_world": True}, partial=True)])
    assert [f.rid for f in run("rds.sg_open", v)] == ["open"]


def test_rds_sg_open_ignores_missing_attribute():
    v = FakeView([Resource("rds.instance", "us-east-1", "unknown", {})])
    assert run("rds.sg_open", v) == []


def test_rds_public_flags_a_public_cluster():
    v = FakeView([Resource("rds.cluster", "eu-west-1", "aurora-1",
                           {"publicly_accessible": True})])
    assert [f.rid for f in run("rds.public", v)] == ["aurora-1"]


def test_rds_public_ignores_a_cluster_with_the_attribute_omitted():
    v = FakeView([Resource("rds.cluster", "eu-west-1", "aurora-1", {}, partial=True)])
    assert run("rds.public", v) == []


def test_rds_sg_open_flags_a_cluster_open_to_the_world():
    v = FakeView([Resource("rds.cluster", "eu-west-1", "aurora-1",
                           {"open_to_world": True, "security_groups": ["sg-9"]})])
    findings = run("rds.sg_open", v)
    assert [f.rid for f in findings] == ["aurora-1"]
    assert findings[0].detail["security_groups"] == ["sg-9"]


def test_rds_rules_report_the_resource_type_they_fired_on():
    v = FakeView([
        Resource("rds.instance", "us-east-1", "db1", {"publicly_accessible": True}),
        Resource("rds.cluster", "eu-west-1", "aurora-1",
                 {"publicly_accessible": True})])
    assert {f.rtype for f in run("rds.public", v)} == {"rds.instance", "rds.cluster"}


# ---------------------------------------------------------------------------
# s3.py
# ---------------------------------------------------------------------------

def test_s3_anonymous_read_flags_public_policy():
    v = FakeView([
        Resource("s3.bucket", "global", "open", {"policy_public": True}),
        Resource("s3.bucket", "global", "shut", {"policy_public": False})])
    found = run("s3.anonymous_read", v)
    assert [f.rid for f in found] == ["open"] and found[0].severity == "critical"


def test_s3_anonymous_read_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("s3.bucket", "global", "open",
                           {"policy_public": True}, partial=True)])
    assert [f.rid for f in run("s3.anonymous_read", v)] == ["open"]


def test_s3_anonymous_read_ignores_missing_attribute():
    v = FakeView([Resource("s3.bucket", "global", "b", {})])
    assert run("s3.anonymous_read", v) == []


def test_s3_no_public_access_block_flags_absent_block():
    v = FakeView([
        Resource("s3.bucket", "global", "blocked", {"public_access_block": True}),
        Resource("s3.bucket", "global", "open", {"public_access_block": False})])
    assert [f.rid for f in run("s3.no_public_access_block", v)] == ["open"]


def test_s3_no_public_access_block_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("s3.bucket", "global", "open",
                           {"public_access_block": False}, partial=True)])
    assert [f.rid for f in run("s3.no_public_access_block", v)] == ["open"]


def test_s3_no_public_access_block_ignores_missing_attribute():
    v = FakeView([Resource("s3.bucket", "global", "b", {})])
    assert run("s3.no_public_access_block", v) == []


def test_s3_no_encryption_flags_unencrypted_buckets():
    v = FakeView([
        Resource("s3.bucket", "global", "enc", {"encrypted": True}),
        Resource("s3.bucket", "global", "plain", {"encrypted": False})])
    assert [f.rid for f in run("s3.no_encryption", v)] == ["plain"]


def test_s3_no_encryption_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("s3.bucket", "global", "plain",
                           {"encrypted": False}, partial=True)])
    assert [f.rid for f in run("s3.no_encryption", v)] == ["plain"]


def test_s3_no_encryption_ignores_missing_attribute():
    v = FakeView([Resource("s3.bucket", "global", "b", {})])
    assert run("s3.no_encryption", v) == []


def test_s3_no_lifecycle_flags_buckets_without_lifecycle():
    v = FakeView([
        Resource("s3.bucket", "global", "has_lc", {"has_lifecycle": True}),
        Resource("s3.bucket", "global", "no_lc", {"has_lifecycle": False})])
    assert [f.rid for f in run("s3.no_lifecycle", v)] == ["no_lc"]


def test_s3_no_lifecycle_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("s3.bucket", "global", "no_lc",
                           {"has_lifecycle": False}, partial=True)])
    assert [f.rid for f in run("s3.no_lifecycle", v)] == ["no_lc"]


def test_s3_no_lifecycle_ignores_missing_attribute():
    v = FakeView([Resource("s3.bucket", "global", "b", {})])
    assert run("s3.no_lifecycle", v) == []


# ---------------------------------------------------------------------------
# iam.py
# ---------------------------------------------------------------------------

def test_iam_root_no_mfa_is_account_scoped():
    v = FakeView([Resource("account.summary", "global", "self",
                           {"root_mfa_enabled": False, "has_password_policy": True})])
    found = run("iam.root_no_mfa", v)
    assert len(found) == 1 and found[0].rid == "self"


def test_iam_root_no_mfa_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("account.summary", "global", "self",
                           {"root_mfa_enabled": False}, partial=True)])
    assert [f.rid for f in run("iam.root_no_mfa", v)] == ["self"]


def test_iam_root_no_mfa_ignores_missing_attribute():
    v = FakeView([Resource("account.summary", "global", "self",
                           {"has_password_policy": True})])
    assert run("iam.root_no_mfa", v) == []


def test_iam_no_password_policy_flags_absent_policy():
    v = FakeView([Resource("account.summary", "global", "self",
                           {"has_password_policy": False})])
    assert len(run("iam.no_password_policy", v)) == 1


def test_iam_no_password_policy_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("account.summary", "global", "self",
                           {"has_password_policy": False}, partial=True)])
    assert [f.rid for f in run("iam.no_password_policy", v)] == ["self"]


def test_iam_no_password_policy_ignores_missing_attribute():
    v = FakeView([Resource("account.summary", "global", "self", {})])
    assert run("iam.no_password_policy", v) == []


def test_iam_user_no_mfa_flags_users_without_mfa():
    v = FakeView([
        Resource("iam.user", "global", "alice", {"has_mfa": False}),
        Resource("iam.user", "global", "bob", {"has_mfa": True})])
    assert [f.rid for f in run("iam.user_no_mfa", v)] == ["alice"]


def test_iam_user_no_mfa_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("iam.user", "global", "alice",
                           {"has_mfa": False}, partial=True)])
    assert [f.rid for f in run("iam.user_no_mfa", v)] == ["alice"]


def test_iam_user_no_mfa_ignores_missing_attribute():
    v = FakeView([Resource("iam.user", "global", "alice", {})])
    assert run("iam.user_no_mfa", v) == []


def test_iam_key_stale_respects_the_max_age_param():
    old = Resource("iam.access_key", "global", "AKIA1",
                   {"status": "Active", "created": "2020-01-01T00:00:00Z"})
    v = FakeView([old])
    rule = get_rule("iam.key_stale")
    assert list(rule.evaluate(v, {"max_age_days": 180}))
    assert not list(rule.evaluate(v, {"max_age_days": 100000}))


def test_iam_key_stale_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("iam.access_key", "global", "AKIA1",
                           {"status": "Active", "created": "2020-01-01T00:00:00Z"},
                           partial=True)])
    rule = get_rule("iam.key_stale")
    assert [f.rid for f in rule.evaluate(v, {"max_age_days": 1})] == ["AKIA1"]


def test_iam_key_stale_ignores_missing_attribute():
    v = FakeView([Resource("iam.access_key", "global", "AKIA1", {"status": "Active"})])
    rule = get_rule("iam.key_stale")
    assert list(rule.evaluate(v, {"max_age_days": 1})) == []


def test_iam_key_unused_flags_a_never_used_key():
    v = FakeView([Resource("iam.access_key", "global", "AKIA-NEVER",
                           {"status": "Active", "created": "2024-01-01T00:00:00Z",
                            "last_used": None})])
    rule = get_rule("iam.key_unused")
    found = list(rule.evaluate(v, {"unused_days": 365}))
    assert len(found) == 1
    assert found[0].detail["reason"] == "never_used"


def test_iam_key_unused_ignores_a_recently_used_key():
    from datetime import UTC, datetime, timedelta
    recent = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    v = FakeView([Resource("iam.access_key", "global", "AKIA-FRESH",
                           {"status": "Active", "created": "2024-01-01T00:00:00Z",
                            "last_used": recent})])
    rule = get_rule("iam.key_unused")
    assert list(rule.evaluate(v, {"unused_days": 365})) == []


def test_iam_key_unused_flags_a_long_unused_key():
    v = FakeView([Resource("iam.access_key", "global", "AKIA-STALE",
                           {"status": "Active", "created": "2020-01-01T00:00:00Z",
                            "last_used": "2020-06-01T00:00:00Z"})])
    rule = get_rule("iam.key_unused")
    found = list(rule.evaluate(v, {"unused_days": 365}))
    assert len(found) == 1
    assert found[0].detail["reason"] == "last_used_stale"


def test_iam_key_unused_respects_the_unused_days_param():
    from datetime import UTC, datetime, timedelta
    last_used = (datetime.now(UTC) - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    v = FakeView([Resource("iam.access_key", "global", "AKIA-STALE",
                           {"status": "Active", "created": "2020-01-01T00:00:00Z",
                            "last_used": last_used})])
    rule = get_rule("iam.key_unused")
    assert list(rule.evaluate(v, {"unused_days": 365}))
    assert not list(rule.evaluate(v, {"unused_days": 100000}))


def test_iam_key_unused_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("iam.access_key", "global", "AKIA1",
                           {"status": "Active", "created": "2020-01-01T00:00:00Z",
                            "last_used": None}, partial=True)])
    rule = get_rule("iam.key_unused")
    assert [f.rid for f in rule.evaluate(v, {"unused_days": 1})] == ["AKIA1"]


def test_iam_key_unused_ignores_missing_attribute():
    v = FakeView([Resource("iam.access_key", "global", "AKIA1",
                           {"status": "Active", "created": "2020-01-01T00:00:00Z"})])
    rule = get_rule("iam.key_unused")
    assert list(rule.evaluate(v, {"unused_days": 1})) == []


def test_iam_key_unused_ignores_inactive_keys():
    v = FakeView([Resource("iam.access_key", "global", "AKIA1",
                           {"status": "Inactive", "created": "2020-01-01T00:00:00Z",
                            "last_used": None})])
    rule = get_rule("iam.key_unused")
    assert list(rule.evaluate(v, {"unused_days": 1})) == []


# ---------------------------------------------------------------------------
# ec2.py
# ---------------------------------------------------------------------------

def test_ec2_sg_open_admin_port_flags_configured_ports():
    v = FakeView([
        Resource("ec2.security_group", "us-east-1", "sg-open",
                 {"open_ports": [22, 80], "all_ports_open": False}),
        Resource("ec2.security_group", "us-east-1", "sg-shut",
                 {"open_ports": [80], "all_ports_open": False})])
    rule = get_rule("ec2.sg_open_admin_port")
    found = list(rule.evaluate(v, {"ports": [22, 3389]}))
    assert [f.rid for f in found] == ["sg-open"]


def test_ec2_sg_open_admin_port_flags_all_ports_open():
    v = FakeView([Resource("ec2.security_group", "us-east-1", "sg-any",
                           {"open_ports": [], "all_ports_open": True})])
    rule = get_rule("ec2.sg_open_admin_port")
    found = list(rule.evaluate(v, {"ports": [22, 3389]}))
    assert [f.rid for f in found] == ["sg-any"]


def test_ec2_sg_open_admin_port_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("ec2.security_group", "us-east-1", "sg-open",
                           {"open_ports": [22], "all_ports_open": False},
                           partial=True)])
    rule = get_rule("ec2.sg_open_admin_port")
    assert [f.rid for f in rule.evaluate(v, rule.params)] == ["sg-open"]


def test_ec2_sg_open_admin_port_ignores_missing_attribute():
    v = FakeView([Resource("ec2.security_group", "us-east-1", "sg",
                           {"all_ports_open": False})])
    rule = get_rule("ec2.sg_open_admin_port")
    assert list(rule.evaluate(v, rule.params)) == []


def test_waste_idle_resource_flags_stopped_instance_with_ebs():
    v = FakeView([
        Resource("ec2.instance", "us-east-1", "i-stopped",
                 {"state": "stopped", "attached_volume_gb": 20}),
        Resource("ec2.instance", "us-east-1", "i-running",
                 {"state": "running", "attached_volume_gb": 20})])
    assert [f.rid for f in run("waste.idle_resource", v)] == ["i-stopped"]


def test_waste_idle_resource_flags_unattached_volume():
    v = FakeView([
        Resource("ec2.volume", "us-east-1", "vol-free",
                 {"state": "available", "attached_to": None}),
        Resource("ec2.volume", "us-east-1", "vol-used",
                 {"state": "in-use", "attached_to": "i-123"})])
    assert [f.rid for f in run("waste.idle_resource", v)] == ["vol-free"]


def test_waste_idle_resource_flags_partial_instance_when_attribute_present():
    v = FakeView([Resource("ec2.instance", "us-east-1", "i-stopped",
                           {"state": "stopped", "attached_volume_gb": 20},
                           partial=True)])
    assert [f.rid for f in run("waste.idle_resource", v)] == ["i-stopped"]


def test_waste_idle_resource_ignores_missing_instance_attribute():
    v = FakeView([Resource("ec2.instance", "us-east-1", "i-x", {"state": "stopped"})])
    assert run("waste.idle_resource", v) == []


def test_waste_idle_resource_flags_partial_volume_when_attribute_present():
    v = FakeView([Resource("ec2.volume", "us-east-1", "vol-free",
                           {"attached_to": None}, partial=True)])
    assert [f.rid for f in run("waste.idle_resource", v)] == ["vol-free"]


def test_waste_idle_resource_ignores_missing_volume_attribute():
    v = FakeView([Resource("ec2.volume", "us-east-1", "vol-x", {"state": "available"})])
    assert run("waste.idle_resource", v) == []


# ---------------------------------------------------------------------------
# lambda_.py
# ---------------------------------------------------------------------------

def test_lambda_deprecated_runtime_uses_configurable_list():
    v = FakeView([
        Resource("lambda.function", "us-east-1", "old", {"runtime": "python3.8"}),
        Resource("lambda.function", "us-east-1", "new", {"runtime": "python3.12"})])
    rule = get_rule("lambda.deprecated_runtime")
    found = list(rule.evaluate(v, {"deprecated": ["python3.8"]}))
    assert [f.rid for f in found] == ["old"]


def test_lambda_deprecated_runtime_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("lambda.function", "us-east-1", "old",
                           {"runtime": "python3.8"}, partial=True)])
    rule = get_rule("lambda.deprecated_runtime")
    found = list(rule.evaluate(v, {"deprecated": ["python3.8"]}))
    assert [f.rid for f in found] == ["old"]


def test_lambda_deprecated_runtime_ignores_missing_attribute():
    v = FakeView([Resource("lambda.function", "us-east-1", "old", {})])
    rule = get_rule("lambda.deprecated_runtime")
    assert list(rule.evaluate(v, {"deprecated": ["python3.8"]})) == []


def test_lambda_deprecated_runtime_does_not_flag_container_image_functions():
    # BUG 1 - container-image functions carry runtime "(container)"; that is
    # not a deprecated managed runtime and must never be flagged as one.
    v = FakeView([Resource("lambda.function", "us-east-1", "img",
                           {"runtime": "(container)"})])
    rule = get_rule("lambda.deprecated_runtime")
    assert list(rule.evaluate(v, rule.params)) == []


# ---------------------------------------------------------------------------
# logs.py
# ---------------------------------------------------------------------------

def test_logs_no_retention_flags_groups_with_none_retention():
    v = FakeView([
        Resource("logs.group", "us-east-1", "forever", {"retention_days": None}),
        Resource("logs.group", "us-east-1", "expires", {"retention_days": 30})])
    assert [f.rid for f in run("logs.no_retention", v)] == ["forever"]


def test_logs_no_retention_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("logs.group", "us-east-1", "forever",
                           {"retention_days": None}, partial=True)])
    assert [f.rid for f in run("logs.no_retention", v)] == ["forever"]


def test_logs_no_retention_ignores_missing_attribute():
    v = FakeView([Resource("logs.group", "us-east-1", "g", {})])
    assert run("logs.no_retention", v) == []


# ---------------------------------------------------------------------------
# cloudtrail.py
# ---------------------------------------------------------------------------

def test_cloudtrail_not_logging_flags_dark_trails():
    v = FakeView([
        Resource("cloudtrail.trail", "us-east-1", "live", {"is_logging": True}),
        Resource("cloudtrail.trail", "us-east-1", "dark", {"is_logging": False})])
    assert [f.rid for f in run("cloudtrail.not_logging", v)] == ["dark"]


def test_cloudtrail_not_logging_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("cloudtrail.trail", "us-east-1", "dark",
                           {"is_logging": False}, partial=True)])
    assert [f.rid for f in run("cloudtrail.not_logging", v)] == ["dark"]


def test_cloudtrail_not_logging_ignores_missing_attribute():
    v = FakeView([Resource("cloudtrail.trail", "us-east-1", "t", {})])
    assert run("cloudtrail.not_logging", v) == []


# ---------------------------------------------------------------------------
# guardrails.py
# ---------------------------------------------------------------------------

def test_guardrails_disabled_flags_each_absent_service():
    v = FakeView([
        Resource("guardrails.service", "us-east-1", "guardduty", {"enabled": False}),
        Resource("guardrails.service", "us-east-1", "securityhub", {"enabled": True})])
    assert [f.rid for f in run("guardrails.disabled", v)] == ["guardduty"]


def test_guardrails_disabled_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("guardrails.service", "us-east-1", "guardduty",
                           {"enabled": False}, partial=True)])
    assert [f.rid for f in run("guardrails.disabled", v)] == ["guardduty"]


def test_guardrails_disabled_ignores_missing_attribute():
    v = FakeView([Resource("guardrails.service", "us-east-1", "guardduty", {})])
    assert run("guardrails.disabled", v) == []


# ---------------------------------------------------------------------------
# cost.py
# ---------------------------------------------------------------------------

def test_freetier_exceeded_flags_usage_at_or_over_limit():
    from aws95.aws.engines.labsnap.model import Metric
    v = FakeView(metrics=[
        Metric("freetier.usage", 100.0, dims={"service": "AmazonEC2",
               "usage_type": "BoxUsage", "region": "us-east-1", "day_of_month": 15}),
        Metric("freetier.usage", 42.0, dims={"service": "AmazonS3",
               "usage_type": "Storage", "region": "us-east-1", "day_of_month": 15})])
    found = run("freetier.exceeded", v)
    assert len(found) == 1 and found[0].detail["service"] == "AmazonEC2"


def test_freetier_exceeded_ignores_absent_metric():
    v = FakeView(metrics=[])
    assert run("freetier.exceeded", v) == []


def test_budget_ineffective_flags_tag_filtered_budget_with_no_allocation_tags():
    from aws95.aws.engines.labsnap.model import Metric
    v = FakeView(
        resources=[Resource("budgets.budget", "global", "team-x",
                            {"filters_on_tag": True})],
        metrics=[Metric("cost.active_allocation_tags", 0.0)])
    assert [f.rid for f in run("budget.ineffective", v)] == ["team-x"]


def test_budget_ineffective_ignores_budget_when_tags_exist():
    from aws95.aws.engines.labsnap.model import Metric
    v = FakeView(
        resources=[Resource("budgets.budget", "global", "team-x",
                            {"filters_on_tag": True})],
        metrics=[Metric("cost.active_allocation_tags", 3.0)])
    assert run("budget.ineffective", v) == []


def test_budget_ineffective_flags_partial_resource_when_attribute_present():
    from aws95.aws.engines.labsnap.model import Metric
    v = FakeView(
        resources=[Resource("budgets.budget", "global", "team-x",
                            {"filters_on_tag": True}, partial=True)],
        metrics=[Metric("cost.active_allocation_tags", 0.0)])
    assert [f.rid for f in run("budget.ineffective", v)] == ["team-x"]


def test_budget_ineffective_ignores_missing_attribute():
    v = FakeView(resources=[Resource("budgets.budget", "global", "team-x", {})])
    assert run("budget.ineffective", v) == []


def test_budget_ineffective_ignores_when_allocation_tag_metric_absent():
    # The tag-count metric itself was never collected (denial) - we cannot
    # tell whether the budget is ineffective, so no finding is emitted.
    v = FakeView(
        resources=[Resource("budgets.budget", "global", "team-x",
                            {"filters_on_tag": True})],
        metrics=[])
    assert run("budget.ineffective", v) == []



# ---------------------------------------------------------------------------
# elb.py
# ---------------------------------------------------------------------------

def test_elb_listener_plaintext_flags_http_on_an_internet_facing_lb():
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 80, "protocol": "HTTP", "ssl_policy": None}]})])
    findings = run("elb.listener_plaintext", v)
    assert [f.rid for f in findings] == ["web"]
    assert findings[0].detail["ports"] == [80]


def test_elb_listener_plaintext_ignores_an_internal_lb():
    # An internal plaintext listener is an ordinary architecture choice; the
    # rule is about exposure to the internet, not about TLS everywhere.
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "internal", {
        "scheme": "internal",
        "listeners": [{"port": 80, "protocol": "HTTP", "ssl_policy": None}]})])
    assert run("elb.listener_plaintext", v) == []


def test_elb_listener_plaintext_flags_tcp_by_default():
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 443, "protocol": "TCP", "ssl_policy": None}]})])
    assert [f.rid for f in run("elb.listener_plaintext", v)] == ["web"]


def test_elb_listener_plaintext_respects_a_narrower_protocol_override():
    # An internet-facing NLB doing TLS passthrough on TCP is not actually
    # plaintext - the default flags TCP, but an operator can narrow the
    # protocol set to exclude it without editing the rule.
    rule = get_rule("elb.listener_plaintext")
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 443, "protocol": "TCP", "ssl_policy": None}]})])
    assert list(rule.evaluate(v, {"plaintext_protocols": ["HTTP"]})) == []


def test_elb_listener_plaintext_ignores_a_lb_whose_listeners_were_denied():
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web",
                           {"scheme": "internet-facing"}, partial=True)])
    assert run("elb.listener_plaintext", v) == []


def test_elb_listener_plaintext_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check is the
    # precise guard; a coarser `r.partial` check must not suppress a finding
    # this attribute already answers.
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 80, "protocol": "HTTP", "ssl_policy": None}]},
        partial=True)])
    assert [f.rid for f in run("elb.listener_plaintext", v)] == ["web"]


def test_elb_weak_tls_policy_flags_a_policy_outside_the_approved_set():
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 443, "protocol": "HTTPS",
                       "ssl_policy": "ELBSecurityPolicy-2016-08"}]})])
    findings = run("elb.weak_tls_policy", v)
    assert [f.rid for f in findings] == ["web"]
    assert findings[0].detail["policies"] == ["ELBSecurityPolicy-2016-08"]


def test_elb_weak_tls_policy_accepts_a_modern_policy():
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 443, "protocol": "HTTPS",
                       "ssl_policy": "ELBSecurityPolicy-TLS13-1-2-2021-06"}]})])
    assert run("elb.weak_tls_policy", v) == []


def test_elb_weak_tls_policy_respects_an_approved_override():
    rule = get_rule("elb.weak_tls_policy")
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 443, "protocol": "HTTPS",
                       "ssl_policy": "ELBSecurityPolicy-2016-08"}]})])
    assert list(rule.evaluate(v, {"approved": ["ELBSecurityPolicy-2016-08"]})) == []


def test_elb_weak_tls_policy_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check is the
    # precise guard; a coarser `r.partial` check must not suppress a finding
    # this attribute already answers.
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web", {
        "scheme": "internet-facing",
        "listeners": [{"port": 443, "protocol": "HTTPS",
                       "ssl_policy": "ELBSecurityPolicy-2016-08"}]},
        partial=True)])
    assert [f.rid for f in run("elb.weak_tls_policy", v)] == ["web"]


def test_elb_internet_facing_flags_only_internet_facing():
    v = FakeView([
        Resource("elb.load_balancer", "eu-west-1", "web",
                 {"scheme": "internet-facing"}),
        Resource("elb.load_balancer", "eu-west-1", "internal",
                 {"scheme": "internal"})])
    assert [f.rid for f in run("elb.internet_facing", v)] == ["web"]


def test_elb_internet_facing_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("elb.load_balancer", "eu-west-1", "web",
                           {"scheme": "internet-facing"}, partial=True)])
    assert [f.rid for f in run("elb.internet_facing", v)] == ["web"]


def test_elb_no_targets_flags_an_empty_target_group():
    v = FakeView([
        Resource("elb.target_group", "eu-west-1", "empty-tg",
                 {"total_target_count": 0}),
        Resource("elb.target_group", "eu-west-1", "used-tg",
                 {"total_target_count": 3})])
    assert [f.rid for f in run("elb.no_targets", v)] == ["empty-tg"]


def test_elb_no_targets_ignores_a_group_whose_health_was_denied():
    v = FakeView([Resource("elb.target_group", "eu-west-1", "unknown-tg",
                           {}, partial=True)])
    assert run("elb.no_targets", v) == []


def test_elb_no_targets_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("elb.target_group", "eu-west-1", "empty-tg",
                           {"total_target_count": 0}, partial=True)])
    assert [f.rid for f in run("elb.no_targets", v)] == ["empty-tg"]


# ---------------------------------------------------------------------------
# kms.py
# ---------------------------------------------------------------------------

def test_kms_rotation_disabled_flags_a_customer_key_without_rotation():
    v = FakeView([Resource("kms.key", "eu-west-1", "cmk-1",
                           {"manager": "CUSTOMER", "rotation_enabled": False})])
    assert [f.rid for f in run("kms.rotation_disabled", v)] == ["cmk-1"]


def test_kms_rotation_disabled_ignores_an_aws_managed_key():
    v = FakeView([Resource("kms.key", "eu-west-1", "aws-1",
                           {"manager": "AWS"})])
    assert run("kms.rotation_disabled", v) == []


def test_kms_rotation_disabled_ignores_a_key_whose_rotation_was_denied():
    v = FakeView([Resource("kms.key", "eu-west-1", "cmk-1",
                           {"manager": "CUSTOMER"}, partial=True)])
    assert run("kms.rotation_disabled", v) == []


def test_kms_rotation_disabled_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check is the
    # precise guard; a coarser `r.partial` check must not suppress a finding
    # this attribute already answers.
    v = FakeView([Resource("kms.key", "eu-west-1", "cmk-1",
                           {"manager": "CUSTOMER", "rotation_enabled": False},
                           partial=True)])
    assert [f.rid for f in run("kms.rotation_disabled", v)] == ["cmk-1"]


def test_kms_policy_allows_external_flags_only_when_true():
    v = FakeView([
        Resource("kms.key", "eu-west-1", "open", {"policy_allows_external": True}),
        Resource("kms.key", "eu-west-1", "closed", {"policy_allows_external": False}),
        Resource("kms.key", "eu-west-1", "unknown", {}, partial=True)])
    assert [f.rid for f in run("kms.policy_allows_external", v)] == ["open"]


def test_kms_policy_allows_external_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("kms.key", "eu-west-1", "open",
                           {"policy_allows_external": True}, partial=True)])
    assert [f.rid for f in run("kms.policy_allows_external", v)] == ["open"]


# ---------------------------------------------------------------------------
# secrets.py
# ---------------------------------------------------------------------------

def test_secrets_rotation_disabled_flags_only_unrotated():
    v = FakeView([
        Resource("secrets.secret", "eu-west-1", "app/db",
                 {"rotation_enabled": True}),
        Resource("secrets.secret", "eu-west-1", "legacy/api",
                 {"rotation_enabled": False})])
    assert [f.rid for f in run("secrets.rotation_disabled", v)] == ["legacy/api"]


def test_secrets_rotation_disabled_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check is the
    # precise guard; a coarser `r.partial` check must not suppress a finding
    # this attribute already answers.
    v = FakeView([Resource("secrets.secret", "eu-west-1", "legacy/api",
                           {"rotation_enabled": False}, partial=True)])
    assert [f.rid for f in run("secrets.rotation_disabled", v)] == ["legacy/api"]


def test_secrets_unused_flags_a_secret_past_the_idle_threshold():
    v = FakeView([
        Resource("secrets.secret", "eu-west-1", "fresh",
                 {"days_since_accessed": 3}),
        Resource("secrets.secret", "eu-west-1", "stale",
                 {"days_since_accessed": 400})])
    findings = run("secrets.unused", v)
    assert [f.rid for f in findings] == ["stale"]
    assert findings[0].detail["days_since_accessed"] == 400


def test_secrets_unused_flags_a_secret_that_was_never_accessed():
    # None here means "never accessed", which is more of a finding than
    # "accessed a long time ago" - it must not be silently skipped.
    v = FakeView([Resource("secrets.secret", "eu-west-1", "never",
                           {"days_since_accessed": None})])
    findings = run("secrets.unused", v)
    assert [f.rid for f in findings] == ["never"]
    assert findings[0].detail["days_since_accessed"] is None


def test_secrets_unused_respects_a_tighter_threshold():
    rule = get_rule("secrets.unused")
    v = FakeView([Resource("secrets.secret", "eu-west-1", "month-old",
                           {"days_since_accessed": 45})])
    assert [f.rid for f in rule.evaluate(v, {"max_idle_days": 30})] == ["month-old"]
    assert list(rule.evaluate(v, {"max_idle_days": 90})) == []


def test_secrets_unused_ignores_a_secret_with_the_attribute_missing():
    v = FakeView([Resource("secrets.secret", "eu-west-1", "unknown", {})])
    assert run("secrets.unused", v) == []


def test_secrets_unused_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("secrets.secret", "eu-west-1", "stale",
                           {"days_since_accessed": 400}, partial=True)])
    assert [f.rid for f in run("secrets.unused", v)] == ["stale"]


# ---------------------------------------------------------------------------
# ecr.py
# ---------------------------------------------------------------------------

def test_ecr_public_policy_flags_only_public_repositories():
    v = FakeView([
        Resource("ecr.repository", "eu-west-1", "open",
                 {"policy_allows_public": True}),
        Resource("ecr.repository", "eu-west-1", "closed",
                 {"policy_allows_public": False}),
        Resource("ecr.repository", "eu-west-1", "unknown", {}, partial=True)])
    assert [f.rid for f in run("ecr.public_policy", v)] == ["open"]


def test_ecr_public_policy_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("ecr.repository", "eu-west-1", "open",
                           {"policy_allows_public": True}, partial=True)])
    assert [f.rid for f in run("ecr.public_policy", v)] == ["open"]


def test_ecr_scan_on_push_off_flags_only_when_disabled():
    v = FakeView([
        Resource("ecr.repository", "eu-west-1", "scanned", {"scan_on_push": True}),
        Resource("ecr.repository", "eu-west-1", "unscanned", {"scan_on_push": False})])
    assert [f.rid for f in run("ecr.scan_on_push_off", v)] == ["unscanned"]


def test_ecr_scan_on_push_off_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("ecr.repository", "eu-west-1", "unscanned",
                           {"scan_on_push": False}, partial=True)])
    assert [f.rid for f in run("ecr.scan_on_push_off", v)] == ["unscanned"]


def test_ecr_mutable_tags_flags_only_mutable():
    v = FakeView([
        Resource("ecr.repository", "eu-west-1", "mutable",
                 {"tag_mutability": "MUTABLE"}),
        Resource("ecr.repository", "eu-west-1", "immutable",
                 {"tag_mutability": "IMMUTABLE"})])
    assert [f.rid for f in run("ecr.mutable_tags", v)] == ["mutable"]


def test_ecr_mutable_tags_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("ecr.repository", "eu-west-1", "mutable",
                           {"tag_mutability": "MUTABLE"}, partial=True)])
    assert [f.rid for f in run("ecr.mutable_tags", v)] == ["mutable"]


# ---------------------------------------------------------------------------
# dynamodb.py
# ---------------------------------------------------------------------------

def test_dynamodb_pitr_off_flags_only_tables_without_recovery():
    v = FakeView([
        Resource("dynamodb.table", "eu-west-1", "orders", {"pitr_enabled": True}),
        Resource("dynamodb.table", "eu-west-1", "sessions", {"pitr_enabled": False})])
    assert [f.rid for f in run("dynamodb.pitr_off", v)] == ["sessions"]


def test_dynamodb_pitr_off_ignores_a_table_whose_backups_were_denied():
    v = FakeView([Resource("dynamodb.table", "eu-west-1", "orders",
                           {"billing_mode": "PROVISIONED"}, partial=True)])
    assert run("dynamodb.pitr_off", v) == []


def test_dynamodb_pitr_off_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("dynamodb.table", "eu-west-1", "sessions",
                           {"pitr_enabled": False}, partial=True)])
    assert [f.rid for f in run("dynamodb.pitr_off", v)] == ["sessions"]


def test_dynamodb_no_deletion_protection_flags_only_unprotected():
    v = FakeView([
        Resource("dynamodb.table", "eu-west-1", "orders",
                 {"deletion_protection": True}),
        Resource("dynamodb.table", "eu-west-1", "sessions",
                 {"deletion_protection": False})])
    assert [f.rid for f in run("dynamodb.no_deletion_protection", v)] == ["sessions"]


def test_dynamodb_no_deletion_protection_flags_partial_resource_when_attribute_present():
    v = FakeView([Resource("dynamodb.table", "eu-west-1", "sessions",
                           {"deletion_protection": False}, partial=True)])
    assert [f.rid for f in run("dynamodb.no_deletion_protection", v)] == ["sessions"]


def test_no_dynamodb_rule_treats_the_aws_owned_key_as_unencrypted():
    # There is no such state: DynamoDB encrypts every table at rest. A rule
    # firing on sse_type == "AWS_OWNED" would be a permanent false positive
    # on every default-configured table in the account.
    v = FakeView([Resource("dynamodb.table", "eu-west-1", "sessions",
                           {"sse_type": "AWS_OWNED", "has_custom_kms_key": False,
                            "pitr_enabled": True, "deletion_protection": True})])
    from aws95.aws.engines.labsnap.rules.base import RuleOverrides
    from aws95.aws.engines.labsnap.rules.base import load_rules
    fired = [f for rule in load_rules(RuleOverrides())
             if rule.id.startswith("dynamodb.")
             for f in rule.evaluate(v, rule.params)]
    assert fired == []


# ---------------------------------------------------------------------------
# cognito.py
# ---------------------------------------------------------------------------

def test_cognito_mfa_off_flags_only_pools_with_mfa_off():
    v = FakeView([
        Resource("cognito.user_pool", "eu-west-1", "eu-west-1_Aaaaaaaaa",
                 {"mfa_configuration": "OFF"}),
        Resource("cognito.user_pool", "eu-west-1", "eu-west-1_Bbbbbbbbb",
                 {"mfa_configuration": "ON"}),
        Resource("cognito.user_pool", "eu-west-1", "eu-west-1_Ccccccccc",
                 {"mfa_configuration": "OPTIONAL"})])
    findings = run("cognito.mfa_off", v)
    assert [f.rid for f in findings] == ["eu-west-1_Aaaaaaaaa"]


def test_cognito_mfa_off_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers. These pools hold customer
    # authentication, so this is the property that matters most in this
    # file: an unrelated denial must never silently swallow an MFA finding.
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Aaaaaaaaa",
                           {"mfa_configuration": "OFF"}, partial=True)])
    assert [f.rid for f in run("cognito.mfa_off", v)] == ["eu-west-1_Aaaaaaaaa"]


def test_cognito_mfa_off_ignores_a_pool_whose_config_was_denied():
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Aaaaaaaaa", {}, partial=True)])
    assert run("cognito.mfa_off", v) == []


def test_cognito_weak_password_policy_flags_a_short_minimum():
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Aaaaaaaaa", {
                               "password_min_length": 8,
                               "password_requires_uppercase": True,
                               "password_requires_lowercase": True,
                               "password_requires_numbers": True,
                               "password_requires_symbols": True})])
    findings = run("cognito.weak_password_policy", v)
    assert [f.rid for f in findings] == ["eu-west-1_Aaaaaaaaa"]
    assert findings[0].detail["reasons"] == ["min_length 8 < 12"]


def test_cognito_weak_password_policy_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Aaaaaaaaa", {
                               "password_min_length": 8,
                               "password_requires_uppercase": True,
                               "password_requires_lowercase": True,
                               "password_requires_numbers": True,
                               "password_requires_symbols": True}, partial=True)])
    assert [f.rid for f in run("cognito.weak_password_policy", v)] == [
        "eu-west-1_Aaaaaaaaa"]


def test_cognito_weak_password_policy_reports_every_reason_it_found():
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Aaaaaaaaa", {
                               "password_min_length": 6,
                               "password_requires_uppercase": False,
                               "password_requires_lowercase": True,
                               "password_requires_numbers": True,
                               "password_requires_symbols": False})])
    reasons = run("cognito.weak_password_policy", v)[0].detail["reasons"]
    assert reasons == ["min_length 6 < 12", "no uppercase required",
                       "no symbols required"]


def test_cognito_weak_password_policy_accepts_a_compliant_pool():
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Bbbbbbbbb", {
                               "password_min_length": 14,
                               "password_requires_uppercase": True,
                               "password_requires_lowercase": True,
                               "password_requires_numbers": True,
                               "password_requires_symbols": True})])
    assert run("cognito.weak_password_policy", v) == []


def test_cognito_weak_password_policy_respects_a_relaxed_min_length():
    rule = get_rule("cognito.weak_password_policy")
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Aaaaaaaaa", {
                               "password_min_length": 8,
                               "password_requires_uppercase": True,
                               "password_requires_lowercase": True,
                               "password_requires_numbers": True,
                               "password_requires_symbols": True})])
    assert list(rule.evaluate(v, {"min_length": 8, "require_uppercase": True,
                                  "require_lowercase": True,
                                  "require_numbers": True,
                                  "require_symbols": True})) == []


def test_cognito_self_signup_allowed_flags_only_open_pools():
    v = FakeView([
        Resource("cognito.user_pool", "eu-west-1", "eu-west-1_Aaaaaaaaa",
                 {"self_signup_allowed": True}),
        Resource("cognito.user_pool", "eu-west-1", "eu-west-1_Bbbbbbbbb",
                 {"self_signup_allowed": False})])
    assert [f.rid for f in run("cognito.self_signup_allowed", v)] == [
        "eu-west-1_Aaaaaaaaa"]


def test_cognito_self_signup_allowed_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("cognito.user_pool", "eu-west-1",
                           "eu-west-1_Aaaaaaaaa",
                           {"self_signup_allowed": True}, partial=True)])
    assert [f.rid for f in run("cognito.self_signup_allowed", v)] == [
        "eu-west-1_Aaaaaaaaa"]


# ---------------------------------------------------------------------------
# cross-cutting
# ---------------------------------------------------------------------------

def test_every_builtin_rule_has_documentation():
    for rule in load_rules(RuleOverrides()):
        assert rule.title and rule.description, f"{rule.id} is undocumented"
        assert rule.severity in {"critical", "high", "medium", "low"}


def test_no_rule_hardcodes_an_organisation_name():
    import pathlib
    root = pathlib.Path("aws95/aws/engines/labsnap/rules/builtin")
    offenders = find_offenders(root.glob("*.py"))
    assert not offenders, f"organisation specifics leaked into: {offenders}"


# ---------------------------------------------------------------------------
# ecs.py
# ---------------------------------------------------------------------------

def test_ecs_privileged_task_flags_only_privileged_definitions():
    v = FakeView([
        Resource("ecs.task_definition", "eu-west-1", "web:7",
                 {"any_privileged": True, "family": "web"}),
        Resource("ecs.task_definition", "eu-west-1", "api:3",
                 {"any_privileged": False, "family": "api"})])
    assert [f.rid for f in run("ecs.privileged_task", v)] == ["web:7"]


def test_ecs_privileged_task_ignores_a_definition_that_could_not_be_read():
    v = FakeView([Resource("ecs.task_definition", "eu-west-1", "web:7",
                           {}, partial=True)])
    assert run("ecs.privileged_task", v) == []


def test_ecs_privileged_task_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("ecs.task_definition", "eu-west-1", "web:7",
                           {"any_privileged": True, "family": "web"},
                           partial=True)])
    assert [f.rid for f in run("ecs.privileged_task", v)] == ["web:7"]


def test_ecs_public_ip_assigned_flags_only_enabled():
    v = FakeView([
        Resource("ecs.service", "eu-west-1", "prod/web",
                 {"assign_public_ip": "ENABLED"}),
        Resource("ecs.service", "eu-west-1", "prod/api",
                 {"assign_public_ip": "DISABLED"})])
    assert [f.rid for f in run("ecs.public_ip_assigned", v)] == ["prod/web"]


def test_ecs_public_ip_assigned_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("ecs.service", "eu-west-1", "prod/web",
                           {"assign_public_ip": "ENABLED"}, partial=True)])
    assert [f.rid for f in run("ecs.public_ip_assigned", v)] == ["prod/web"]


def test_ecs_plaintext_env_vars_flags_above_the_threshold():
    v = FakeView([
        Resource("ecs.task_definition", "eu-west-1", "web:7",
                 {"plaintext_env_count": 2}),
        Resource("ecs.task_definition", "eu-west-1", "api:3",
                 {"plaintext_env_count": 0})])
    findings = run("ecs.plaintext_env_vars", v)
    assert [f.rid for f in findings] == ["web:7"]
    assert findings[0].detail["plaintext_env_count"] == 2


def test_ecs_plaintext_env_vars_flags_partial_resource_when_attribute_present():
    # `partial` means SOME follow-up call was denied - not necessarily the
    # one that reads this attribute. The attribute-presence check below is
    # the precise guard; a coarser `r.partial` check must not suppress a
    # finding this attribute already answers.
    v = FakeView([Resource("ecs.task_definition", "eu-west-1", "web:7",
                           {"plaintext_env_count": 2}, partial=True)])
    assert [f.rid for f in run("ecs.plaintext_env_vars", v)] == ["web:7"]


def test_ecs_plaintext_env_vars_respects_a_relaxed_threshold():
    rule = get_rule("ecs.plaintext_env_vars")
    v = FakeView([Resource("ecs.task_definition", "eu-west-1", "web:7",
                           {"plaintext_env_count": 2})])
    assert list(rule.evaluate(v, {"max_plain_env": 5})) == []
