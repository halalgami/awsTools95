# Upstream: labsnap/labsnap/rules/builtin/s3.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


@register_rule
class AnonymousRead:
    id = "s3.anonymous_read"
    severity = "critical"
    title = "S3 bucket policy allows public access"
    description = ("The bucket's policy resolves to publicly readable per S3's own "
                   "policy-status evaluation - anyone on the internet can read it.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("s3.bucket"):
            if "policy_public" not in r.attrs:
                continue
            if r.attrs["policy_public"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class NoPublicAccessBlock:
    id = "s3.no_public_access_block"
    severity = "high"
    title = "S3 bucket has no Public Access Block"
    description = ("Without a full Public Access Block, a future policy or ACL "
                   "change can expose the bucket with no independent backstop.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("s3.bucket"):
            if "public_access_block" not in r.attrs:
                continue
            if not r.attrs["public_access_block"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class NoEncryption:
    id = "s3.no_encryption"
    severity = "high"
    title = "S3 bucket has no default encryption configured"
    description = "Objects written without an explicit encryption header land unencrypted."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("s3.bucket"):
            if "encrypted" not in r.attrs:
                continue
            if not r.attrs["encrypted"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class NoLifecycle:
    id = "s3.no_lifecycle"
    severity = "medium"
    title = "S3 bucket has no lifecycle configuration"
    description = ("Without a lifecycle rule, objects accumulate indefinitely at "
                   "standard storage cost instead of transitioning or expiring.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("s3.bucket"):
            if "has_lifecycle" not in r.attrs:
                continue
            if not r.attrs["has_lifecycle"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
