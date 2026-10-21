"""Import History… S3 scope (first-drop spec §5.4): the trail's log location, its day folders, and the two gate
validators that admit only that bucket and that account's CloudTrail keys. An organisation trail's folder is pinned
to the exact organisation id (from organizations.describe_organization, ruling D4); nothing lists the bucket root.
Any other bucket, prefix, account or organisation folder, extension, parameter, traversal or encoding raises
WriteBlockedError before a client exists."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from aws95.aws.gate import Validator, WriteBlockedError

ORG_RE = re.compile(r"o-[a-z0-9]{10,32}")
REGION = r"[a-z]{2}(?:-[a-z]+)+-\d{1,2}"
REGION_RE = re.compile(REGION)
ACCOUNT_RE = re.compile(r"\d{12}")
BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]")
_FILE = r"[A-Za-z0-9_.\-]+\.json\.gz"
_PREFIX_CHARS = re.compile(r"[A-Za-z0-9!_.*'()\-/]*")
_LIST_PARAMS = frozenset({"Bucket", "Prefix", "MaxKeys", "StartAfter", "ContinuationToken", "ExpectedBucketOwner"})
_GET_PARAMS = frozenset({"Bucket", "Key", "Range", "ExpectedBucketOwner"})
_RANGE_RE = re.compile(r"bytes=\d{1,12}-\d{1,12}")


def _safe(path: Any) -> bool:
    """No traversal or encoding tricks: printable ASCII, no '.'/'..' segments, no '//', backslash or '%'."""
    if not isinstance(path, str) or len(path) > 1024 or path.startswith("/"):
        return False
    if any(not 32 < ord(ch) < 127 for ch in path) or "\\" in path or "%" in path or "//" in path:
        return False
    return not any(seg in (".", "..") for seg in path.split("/"))


@dataclass(frozen=True)
class TrailLocation:
    bucket: str
    prefix: str                  # the trail's S3KeyPrefix without surrounding slashes; "" when the trail has none
    account: str                 # whose history: the folder AWSLogs/[<org-id>/]<account>/
    org_id: str | None = None    # an organisation trail's exact o-… folder; None for a plain trail
    owner: str | None = None     # the only ExpectedBucketOwner value a request may carry (the trail's account)

    @property
    def root(self) -> str:
        return f"{self.prefix}/AWSLogs/" if self.prefix else "AWSLogs/"


def location(bucket: str, prefix: str | None, account: str, *, org_id: str | None = None,
             owner: str | None = None) -> TrailLocation:
    """Validate what describe_trails / describe_organization reported before any key is built from it."""
    cleaned = (prefix or "").strip("/")
    if not BUCKET_RE.fullmatch(bucket or ""):
        raise ValueError("unusable bucket name")
    if cleaned and (not _safe(cleaned) or not _PREFIX_CHARS.fullmatch(cleaned)):
        raise ValueError("unusable key prefix")
    if not ACCOUNT_RE.fullmatch(account or ""):
        raise ValueError("not an account id")
    if org_id is not None and not ORG_RE.fullmatch(org_id):
        raise ValueError("not an organisation id")
    if owner is not None and not ACCOUNT_RE.fullmatch(owner):
        raise ValueError("not an account id")
    return TrailLocation(bucket, cleaned, account, org_id, owner)


def _tree(loc: TrailLocation) -> str:
    """'<root>[<org_id>/]<account>/(CloudTrail|CloudTrail-Insight)/' as a regex, with exactly loc.org_id."""
    org = f"{re.escape(loc.org_id)}/" if loc.org_id else ""
    return rf"{re.escape(loc.root)}{org}{loc.account}/(?:CloudTrail|CloudTrail-Insight)/"


def day_prefix(loc: TrailLocation, region: str, day: date, *, insight: bool = False) -> str:
    if not REGION_RE.fullmatch(region):
        raise ValueError("not a region")
    org = f"{loc.org_id}/" if loc.org_id else ""
    kind = "CloudTrail-Insight" if insight else "CloudTrail"
    return f"{loc.root}{org}{loc.account}/{kind}/{region}/{day:%Y/%m/%d}/"


def _refuse(why: str) -> None:
    raise WriteBlockedError(f"s3 import scope: {why}")


def _common(loc: TrailLocation, params: Mapping[str, Any], allowed: frozenset[str]) -> None:
    if set(params) - allowed:
        _refuse("unexpected parameter")
    if params.get("Bucket") != loc.bucket:
        _refuse("other bucket")
    if "ExpectedBucketOwner" in params and (loc.owner is None or params["ExpectedBucketOwner"] != loc.owner):
        _refuse("unexpected bucket owner")


def list_check(loc: TrailLocation) -> Validator:
    tree = re.compile(_tree(loc) + r"[A-Za-z0-9_.\-/]*")

    def check(params: Mapping[str, Any]) -> None:
        _common(loc, params, _LIST_PARAMS)
        prefix = params.get("Prefix")
        if not _safe(prefix) or not tree.fullmatch(prefix):
            _refuse("prefix outside this account's CloudTrail logs")
        after = params.get("StartAfter")
        if after is not None and (not _safe(after) or not after.startswith(prefix)):
            _refuse("StartAfter outside the prefix")
        token = params.get("ContinuationToken")
        if token is not None and not isinstance(token, str):
            _refuse("bad continuation token")
    return check


def get_check(loc: TrailLocation) -> Validator:
    key_re = re.compile(_tree(loc) + rf"{REGION}/\d{{4}}/\d{{2}}/\d{{2}}/{_FILE}")

    def check(params: Mapping[str, Any]) -> None:
        _common(loc, params, _GET_PARAMS)
        key = params.get("Key")
        if not _safe(key) or not key_re.fullmatch(key):
            _refuse("key outside this account's CloudTrail log files")
        rng = params.get("Range")
        if rng is not None and (not isinstance(rng, str) or not _RANGE_RE.fullmatch(rng)):
            _refuse("bad range")
    return check
