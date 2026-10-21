"""Who a session really is. The account id comes from STS, never from a profile name (spec §2)."""
from __future__ import annotations

from typing import Any

from aws95.aws import gate
from aws95.aws.session import STS_REGION
from aws95.core.cancel import CancelToken


def caller(session: Any, cancel: CancelToken | None = None) -> tuple[str, str]:
    out = gate.call(session, "sts", "get_caller_identity", region=STS_REGION, cancel=cancel)
    return str(out["Account"]), str(out["Arn"])
