from aws95.providers.insights_payload import build_payload, estimate_tokens, scrub_check


def entry(**extra):
    return {"regions_in_scope": 2, "resource_counts": {"ec2.instance": 3}, "open_findings_by_severity":
            {"critical": 0, "high": 1, "medium": 0, "low": 0}, "open_findings_by_rule": {"s3.no_lifecycle": 1},
            "cost_currency": "USD", "cost_mtd": "12.5", "cost_last_month": None,
            "top_services_mtd": [["AWS Lambda", "1.00"]], "activity_events_30d": 10, "risky_events_30d": 1,
            "risky_events_by_rule": {"iam-escalation": 1}, "notes": [], **extra}


def test_labels_in_order():
    p = build_payload("insights", [entry(), entry()])
    assert p["analysis"] == "insights" and [a["label"] for a in p["accounts"]] == ["Account 1", "Account 2"]


def test_clean_payload_passes():
    assert scrub_check(build_payload("advice", [entry()]), {"123456789012", "dev", "corp-admin"}) is None


def test_scrub_refuses_hostile_values():
    """Review Focus 1."""
    bad = [entry(resource_counts={"arn-123456789012-logs": 1}),
           entry(open_findings_by_rule={"custom.123456789012": 1}),
           entry(notes=["see arn:aws:s3:::x"]),
           entry(notes=["from 10.0.0.12"]),
           entry(notes=["fe80::1ff:fe23:4567:890a"]),
           entry(notes=["profile corp-admin"])]
    for e in bad:
        assert scrub_check(build_payload("insights", [e]), {"corp-admin"}) is not None, e


def test_estimate_tokens():
    e = entry()
    assert estimate_tokens(e) >= 1 and estimate_tokens({"a": "x" * 400}) > estimate_tokens({"a": "x"})


def test_profile_names_match_whole_tokens_only():
    """Review 2026-10-21: a profile called "prod" must not refuse a service named "product-analytics"."""
    p = build_payload("insights", [entry(top_services_mtd=[["product-analytics", "29.00"]])])
    assert scrub_check(p, {"prod"}) is None
    assert scrub_check(build_payload("insights", [entry(notes=["the prod account"])]), {"prod"}) is not None
