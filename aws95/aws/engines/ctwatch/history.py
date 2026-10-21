"""Import History… (first-drop spec §5.4; replaces ctwatch's athena.py and the 2a spec §10 Athena design): the
trail's own .json.gz log files, read through the gate under validators that admit only that bucket, that account's
CloudTrail keys and (for an organisation trail) the exact o-… folder from organizations.describe_organization.
This module binds its own S3 and Organizations clients, so the exact-calls scanner sees every pair (review I1).
AWS error MESSAGE text is inspected only to recognise a KMS refusal; it is never shown or stored."""
from __future__ import annotations

import json
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from aws95.aws import gate, permissions
from aws95.aws.engines.ctwatch.fetcher import RepeatedToken
from aws95.aws.engines.ctwatch.s3keys import (ACCOUNT_RE, ORG_RE, TrailLocation, day_prefix, get_check, list_check,
                                              location)
from aws95.aws.errors import classify
from aws95.contracts.activity import GLOBAL_REGION, AccessCheck, ImportPlan, TrailInfo, TrailLookup
from aws95.contracts.common import Status
from aws95.core.cancel import CancelledError

MAX_COMPRESSED = 32 * 1024 * 1024        # real CloudTrail files are a few MB (under ~50 MB unpacked); review Minor 5
MAX_JSON = 128 * 1024 * 1024

NO_TRAIL = ("This account has no CloudTrail trail, so there are no log files to import. "
            "The lookup history (up to 90 days) still works.")
ORG_NOTE = ("This is an organisation trail. Its log files usually live in a bucket in your log-archive account, "
            "which this profile may not be able to read. Choose that account's profile under "
            "“Read the files with”.")
ORG_HINT = "If this is an organisation trail, read the files with the log-archive account's profile."
ORG_DENIED = ("This is an organisation trail. Finding its log folder needs organizations:DescribeOrganization, "
              "which this profile can't call. Add this line to its policy:")
ORG_STOP = ("Finding the organisation trail's log folder needs organizations:DescribeOrganization. Use Check Access "
            "in Import History… to see the line to add.")
LIST_DENIED = "This profile can't list the trail's log files in {bucket}. Add these lines to its policy:"
GET_DENIED = "This profile can list the log files but can't read them. Add this line to its policy:"
GET_DENIED_KMS = ("Reading a log file was denied. The trail is encrypted, so the profile needs both s3:GetObject "
                  "and kms:Decrypt:")
KMS_DENIED = ("The trail's log files are encrypted with a KMS key this profile can't use. Add kms:Decrypt on that "
              "key (the key's own policy must allow it too):")
NO_FILES = ("Listing works, but no log files were found for the newest days of that range, so reading one "
            "couldn't be checked. The import may find nothing.")
ACCESS_OK = "Access checked: this profile can list and read the trail's log files."
ACCESS_OK_KMS = ACCESS_OK + " The files are KMS-encrypted and decrypting works."
BAD_LOCATION = "The trail's bucket, key prefix or organisation id can't be used safely, so nothing was read."
KMS_STOP = ("Reading the trail's files needs kms:Decrypt on the trail's key. Use Check Access in Import History… "
            "to see the line to add.")
DENIED_STOP = "Reading the trail's files was denied. Use Check Access in Import History… to see the lines to add."


class BadLogFile(Exception):
    """A listed file that is not a readable CloudTrail log (not gzip, truncated, not JSON, too large, no Records)."""


class OrgLookupDenied(Exception):
    """organizations:DescribeOrganization was refused: the organisation folder can't be pinned, so nothing is read."""


def trail_info(raw: dict) -> TrailInfo | None:
    bucket, arn = raw.get("S3BucketName"), str(raw.get("TrailARN") or "")
    if not bucket or not arn:
        return None
    parts = arn.split(":")
    return TrailInfo(name=str(raw.get("Name") or ""), arn=arn, home_region=str(raw.get("HomeRegion") or GLOBAL_REGION),
                     bucket=str(bucket), prefix=str(raw.get("S3KeyPrefix") or "").strip("/"),
                     kms_key_id=raw.get("KmsKeyId") or None, is_org=bool(raw.get("IsOrganizationTrail")),
                     trail_account=parts[4] if len(parts) > 5 else "", multi_region=bool(raw.get("IsMultiRegionTrail")),
                     insights=bool(raw.get("HasInsightSelectors")))


def find_trails(session: Any, account: str, regions: Sequence[str], *, cancel=None) -> TrailLookup:
    found: dict[str, TrailInfo] = {}
    for region in dict.fromkeys(regions or (GLOBAL_REGION,)):
        trail_api = gate.bind(session, "cloudtrail", region, cancel=cancel)
        for raw in trail_api.describe_trails(includeShadowTrails=True).get("trailList") or ():
            info = trail_info(raw)
            if info is not None:
                found.setdefault(info.arn, info)
    trails = tuple(sorted(found.values(), key=lambda t: (not t.multi_region, t.trail_account != account, t.name)))
    if not trails:
        return TrailLookup(account, (), NO_TRAIL)
    first = trails[0]
    return TrailLookup(account, trails, ORG_NOTE if first.is_org or first.trail_account != account else None)


def open_s3(session: Any, region: str, *, cancel=None) -> gate.Bound:
    """The engine's only S3 binding (the scanner reads the pairs from this name in this module)."""
    s3 = gate.bind(session, "s3", region, cancel=cancel)
    return s3


def organization_id(session: Any, *, cancel=None) -> str:
    """Ruling D4: the exact o-… folder of an organisation trail. Only Organization.Id is read; the reply also
    carries the management account's e-mail, which is never kept."""
    orgs = gate.bind(session, "organizations", GLOBAL_REGION, cancel=cancel)
    org_id = str((orgs.describe_organization().get("Organization") or {}).get("Id") or "")
    if not ORG_RE.fullmatch(org_id):
        raise ValueError("unusable organisation id")
    return org_id


def resolve_location(session: Any, plan: ImportPlan, *, cancel=None) -> TrailLocation:
    """Where this plan may read: the trail's bucket and prefix, this account's folder, the exact org folder.
    Raises OrgLookupDenied (DescribeOrganization refused), ValueError (unusable values) or the AWS error."""
    trail = plan.trail
    org_id = None
    if trail.is_org:
        try:
            org_id = organization_id(session, cancel=cancel)
        except (CancelledError, ValueError):
            raise
        except Exception as exc:  # noqa: BLE001 - classified below; no fallback listing (D4)
            if classify(exc, "organizations", "DescribeOrganization").status is Status.DENIED:
                raise OrgLookupDenied(ORG_DENIED) from None
            raise
    owner = trail.trail_account if ACCOUNT_RE.fullmatch(trail.trail_account or "") else None
    return location(trail.bucket, trail.prefix, plan.account.account_id, org_id=org_id, owner=owner)


@contextmanager
def scoped(loc: TrailLocation) -> Iterator[None]:
    """The only window in which s3.list_objects_v2 / s3.get_object can be called: this bucket, this account."""
    with gate.validator("s3", "list_objects_v2", list_check(loc)), gate.validator("s3", "get_object", get_check(loc)):
        yield


def list_keys(s3: Any, loc: TrailLocation, prefix: str, *, limit: int | None = None) -> list[str]:
    params: dict[str, Any] = {"Bucket": loc.bucket, "Prefix": prefix}
    if limit is not None:
        params["MaxKeys"] = limit
    keys: list[str] = []
    seen: set[str] = set()
    while True:
        page = s3.list_objects_v2(**params)
        keys += [str(o["Key"]) for o in page.get("Contents") or () if str(o.get("Key", "")).endswith(".json.gz")]
        token = page.get("NextContinuationToken")
        if limit is not None or not page.get("IsTruncated") or not token:
            return keys
        if token in seen:
            raise RepeatedToken("s3 listing repeated its continuation token")
        seen.add(token)
        params["ContinuationToken"] = token


def read_records(s3: Any, loc: TrailLocation, key: str) -> list[dict]:
    body = s3.get_object(Bucket=loc.bucket, Key=key)["Body"]
    try:
        data = body.read(MAX_COMPRESSED + 1)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if len(data) > MAX_COMPRESSED:
        raise BadLogFile("too large")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        text = inflater.decompress(data, MAX_JSON + 1)
    except zlib.error:
        raise BadLogFile("not gzip") from None
    if len(text) > MAX_JSON or inflater.unconsumed_tail:
        raise BadLogFile("too large")
    if not inflater.eof:
        raise BadLogFile("truncated")
    try:
        doc = json.loads(text)
    except ValueError:
        raise BadLogFile("not JSON") from None
    records = doc.get("Records") if isinstance(doc, dict) else None
    if not isinstance(records, list):
        raise BadLogFile("no Records")
    return [r for r in records if isinstance(r, dict)]


def is_kms_error(exc: BaseException) -> bool:
    error = (getattr(exc, "response", None) or {}).get("Error") or {}
    code, message = str(error.get("Code", "")), str(error.get("Message", "")).lower()
    return code.startswith("KMS.") or "kms" in code.lower() or "kms:decrypt" in message


def check_access(session: Any, plan: ImportPlan, *, cancel=None) -> AccessCheck:
    trail, kms = plan.trail, plan.trail.kms_key_id
    hint = f" {ORG_HINT}" if trail.is_org or trail.trail_account != plan.account.account_id else ""
    needs = {*permissions.IMPORT_NEEDS, *((permissions.ORG_NEED,) if trail.is_org else ())}
    try:
        loc = resolve_location(session, plan, cancel=cancel)
    except CancelledError:
        raise
    except OrgLookupDenied:
        return AccessCheck(False, ORG_DENIED, permissions.import_policy(trail.bucket, trail.prefix, kms,
                                                                        needs={permissions.ORG_NEED}))
    except ValueError:
        return AccessCheck(False, BAD_LOCATION)
    except Exception as exc:  # noqa: BLE001
        return AccessCheck(False, classify(exc, "organizations", "DescribeOrganization").message)
    s3 = open_s3(session, trail.home_region, cancel=cancel)
    with scoped(loc):
        try:
            key = None
            for day in plan.days[:3]:
                for region in plan.regions:
                    found = list_keys(s3, loc, day_prefix(loc, region, day), limit=1)
                    if found:
                        key = found[0]
                        break
                if key is not None:
                    break
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            c = classify(exc, "s3", "ListBucket")
            if c.status is Status.DENIED:
                return AccessCheck(False, LIST_DENIED.format(bucket=loc.bucket) + hint,
                                   permissions.import_policy(loc.bucket, loc.prefix, kms, needs=needs))
            return AccessCheck(False, c.message + hint)
        if key is None:
            return AccessCheck(True, NO_FILES)
        try:
            s3.get_object(Bucket=loc.bucket, Key=key, Range="bytes=0-0")
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if kms and is_kms_error(exc):
                return AccessCheck(False, KMS_DENIED,
                                   permissions.import_policy(loc.bucket, loc.prefix, kms, needs={"kms:Decrypt"}))
            c = classify(exc, "s3", "GetObject")
            if c.status is Status.DENIED:
                get_needs = {"s3:GetObject", "kms:Decrypt"} if kms else {"s3:GetObject"}
                return AccessCheck(False, (GET_DENIED_KMS if kms else GET_DENIED) + hint,
                                   permissions.import_policy(loc.bucket, loc.prefix, kms, needs=get_needs))
            return AccessCheck(False, c.message)
    return AccessCheck(True, ACCESS_OK_KMS if kms else ACCESS_OK)
