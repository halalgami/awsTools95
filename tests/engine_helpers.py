"""Builds stored snapshots for engine and provider tests (no AWS)."""
from __future__ import annotations

from datetime import datetime

from aws95.store import Store

ACCOUNT = "123456789012"


def build_snapshot(store: Store, taken_at: datetime, *, account: str = ACCOUNT, collector: str = "s3",
                   status: str = "ok", requested=("global",), covered=("global",), resources=(), metrics=(),
                   findings=None, rules=(), extra_runs=None, regions=("us-east-1",), denials: int = 0) -> int:
    """One snapshot with one collector run (plus `extra_runs` {name: (status, requested, covered)})."""
    sid = store.begin_snapshot(account, f"arn:aws:iam::{account}:user/auditor", "3.0.0", regions, taken_at=taken_at)
    if resources:
        store.write_resources(sid, collector, list(resources))
    if metrics:
        store.write_metrics(sid, collector, list(metrics), taken_at)
    store.record_collector(sid, collector, status, None if status == "ok" else "us-east-1: AWS error: Boom", 7,
                           list(requested), list(covered), denials)
    for name, (st, req, cov) in (extra_runs or {}).items():
        store.record_collector(sid, name, st, None, 7, list(req), list(cov), 0)
    if findings is not None:
        store.write_findings(sid, list(findings), {f.rule for f in findings} | set(rules))
    store.finish_snapshot(sid)
    return sid
