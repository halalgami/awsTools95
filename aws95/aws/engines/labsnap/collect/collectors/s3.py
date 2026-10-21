# Upstream: labsnap/labsnap/collect/collectors/s3.py @ 1b1d437 — adapted for aws95; owned here from now on.
from decimal import Decimal

from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.fanout import map_ordered
from aws95.aws.engines.labsnap.collect.probe import probe_detailed as _probe_detailed
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


def _pab_attr(value):
    cfg = (value or {}).get("PublicAccessBlockConfiguration", {})
    return {"public_access_block": bool(cfg) and all(cfg.values())}


def _policy_attr(value):
    return {"policy_public": bool((value or {}).get("PolicyStatus", {}).get("IsPublic"))}


def _lifecycle_attr(value):
    return {"has_lifecycle": value is not None}


def _encryption_attr(value):
    return {"encrypted": value is not None}


@register
class S3Collector:
    name = "s3"
    scope = "global"
    timeout_s = 900          # fans out per bucket; measured runs took minutes
    volatile_keys = frozenset({"creation_date"})

    def collect(self, session, region, cancel=None):
        # gate.fast_config(): a short connect timeout and one attempt, so an unreachable region (a documented,
        # recurring condition) costs seconds, not the multi-minute default retry. The client uses the profile's
        # region, as upstream did (review Minor 11); botocore's S3 redirector follows buckets in other regions.
        s3 = gate.bind(session, "s3", getattr(session, "region_name", None), cancel=cancel, fast=True)
        resources, denials = [], 0
        buckets = s3.list_buckets()["Buckets"]

        steps = (
            (s3.get_public_access_block, _pab_attr),
            (s3.get_bucket_policy_status, _policy_attr),
            (s3.get_bucket_lifecycle_configuration, _lifecycle_attr),
            (s3.get_bucket_encryption, _encryption_attr),
        )

        def read_bucket(b):
            name, attrs, partial, denied = b["Name"], {}, False, 0
            for op, apply in steps:
                value, state = _probe_detailed(op, Bucket=name)
                if state == "ok":
                    attrs.update(apply(value))
                    continue
                partial = True
                denied += 1
                if state == "unreachable":
                    break       # the same endpoint serves all four calls: don't pay the timeout four times
            return Resource("s3.bucket", "global", name, attrs, partial), denied

        for outcome in map_ordered(read_bucket, buckets, cancel=cancel):
            if isinstance(outcome, Exception):
                raise outcome
            resource, denied = outcome
            resources.append(resource)
            denials += denied

        metrics = [Metric("s3.bucket_count", Decimal(len(buckets)))]
        return CollectorResult(resources, metrics, ["global"], denials)
