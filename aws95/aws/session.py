"""Per-call boto3 sessions (spec §4.2). Never share a Session across threads; boto3 imported lazily."""
from __future__ import annotations

import threading
from typing import Any

DEFAULT_REGION = "us-east-1"
STS_REGION = "us-east-1"
CE_REGION = "us-east-1"
_IMPORT_LOCK = threading.Lock()


def ensure_imported() -> None:
    """First import of boto3/botocore happens on exactly one thread (several workers start at once: scope bars,
    4 wizard checks). Cheap after the first call. Never called at boot (spec §3 boot cost)."""
    with _IMPORT_LOCK:
        import boto3  # noqa: F401


def session_for(profile: str) -> Any:
    if not profile or not profile.strip():
        raise ValueError("no profile selected")          # never fall back to the default credential chain
    ensure_imported()
    import boto3
    return boto3.Session(profile_name=profile)


def region_for(profile_region: str | None) -> str:
    return profile_region or DEFAULT_REGION
