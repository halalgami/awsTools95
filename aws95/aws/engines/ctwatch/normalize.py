# Upstream: ctwatch/ctwatch/ingest/normalize.py @ 254ff31 — adapted for aws95; owned here from now on.
"""CloudTrail record → aws95.store.EventRow. Two shapes: a LookupEvents item (the record is the JSON string in
`CloudTrailEvent`) and an S3 log-file record (the record itself). Every record is redacted FIRST (review C1): the
risk rules, the stored columns and the stored raw JSON all see only the redacted copy."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from aws95.aws.engines.ctwatch import rules
from aws95.aws.engines.ctwatch.redact import redact
from aws95.aws.engines.ctwatch.scope import identity, is_insight, is_sso_session
from aws95.store import EventRow

Evaluate = Callable[[dict], "tuple[int, str | None]"]
NAME_MAX = 60


def classify_principal(ui: dict) -> tuple[str, str, bool]:
    t = ui.get("type", "Unknown")
    if t == "IAMUser":
        return t, ui.get("userName") or str(ui.get("arn", "")).split("/")[-1] or "unidentified", True
    if t == "Root":
        return t, "root", True
    if t == "AssumedRole":
        if is_sso_session(ui):                 # aws95: an IAM Identity Center session is a person (ruling)
            session = str(ui.get("arn", "")).split("/")[-1] or "unidentified"
            return t, f"{session} (SSO)"[:NAME_MAX], True
        role = ((ui.get("sessionContext") or {}).get("sessionIssuer") or {}).get("userName", "")
        return t, role or str(ui.get("arn", "")).split("/")[-1] or "unidentified", False
    if t in ("SAMLUser", "WebIdentityUser"):      # aws95: federated callers (review I7); SAML is a person
        return t, (ui.get("userName") or ui.get("principalId") or "unidentified")[:NAME_MAX], t == "SAMLUser"
    if t == "AWSService" or (t == "Unknown" and ui.get("invokedBy")):
        return "AWSService", ui.get("invokedBy", "service"), False
    if ui.get("principalId") == "Anonymous":
        # Unauthenticated end-user API traffic (Cognito InitiateAuth, RevokeToken): neither staff nor a service
        # role, so it gets its own type and is not counted as human.
        return "Anonymous", "anonymous", False
    return t, (ui.get("arn") or ui.get("principalId") or "unidentified")[:NAME_MAX], False


def extract_arns(detail: dict, resources: list[dict] | None) -> tuple[str, ...]:
    """LookupEvents' Resources list."""
    return tuple(str(r["ResourceName"]) for r in (resources or []) if isinstance(r, dict) and r.get("ResourceName"))


def record_arns(detail: dict) -> tuple[str, ...]:
    """An S3 log record's own resources list."""
    return tuple(str(r["ARN"]) for r in (detail.get("resources") or []) if isinstance(r, dict) and r.get("ARN"))


def _utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _row(original: dict, *, event_id: str, when: Any, account: str, region: str, source: str,
         name: str, arns: tuple[str, ...], origin: Literal["lookup", "import"], evaluate: Evaluate) -> EventRow:
    detail = redact(original)                      # before evaluate, before raw_json (review C1)
    raw_json = json.dumps(detail, separators=(",", ":"), sort_keys=True)
    if is_insight(detail):
        ptype, pname, human, level, rule = "Insight", "CloudTrail Insights", False, 0, None
    else:
        ptype, pname, human = classify_principal(identity(detail))
        level, rule = evaluate(detail)
    return EventRow(event_id=event_id, account=account, region=str(detail.get("awsRegion") or region),
                    time_utc=_utc(when), principal_type=ptype, principal_name=pname, is_human=human, source=source,
                    name=name, read_only=bool(detail.get("readOnly", False)),
                    error_code=detail.get("errorCode") or None, source_ip=detail.get("sourceIPAddress") or None,
                    user_agent=detail.get("userAgent") or None, resource_arns=arns, risk_level=int(level),
                    risk_rule=rule, origin=origin, raw_json=raw_json)


def normalize_lookup(raw: dict, detail: dict, *, account: str, region: str,
                     evaluate: Evaluate = rules.evaluate) -> EventRow:
    return _row(detail, event_id=str(raw["EventId"]), when=raw["EventTime"],
                account=account, region=region,
                source=str(raw.get("EventSource") or detail.get("eventSource", "")),
                name=str(raw.get("EventName") or detail.get("eventName", "")),
                arns=extract_arns(detail, raw.get("Resources")), origin="lookup", evaluate=evaluate)


def normalize(raw: dict, evaluate: Evaluate = rules.evaluate, *, account: str = "", region: str = "") -> EventRow:
    """Upstream entry point: one LookupEvents item, its JSON parsed here."""
    return normalize_lookup(raw, json.loads(raw["CloudTrailEvent"]), account=account, region=region,
                            evaluate=evaluate)


def normalize_record(detail: dict, *, account: str, region: str, evaluate: Evaluate = rules.evaluate) -> EventRow:
    return _row(detail, event_id=str(detail["eventID"]), when=detail["eventTime"], account=account,
                region=region, source=str(detail.get("eventSource", "")), name=str(detail.get("eventName", "")),
                arns=record_arns(detail), origin="import", evaluate=evaluate)
