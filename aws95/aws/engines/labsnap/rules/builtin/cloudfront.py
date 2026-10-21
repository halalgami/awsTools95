"""CloudFront rules (edge-dns spec §4). A rule never fires on an attribute the collector couldn't read."""
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule


def _distributions(snap):
    return snap.resources("cloudfront.distribution")


@register_rule
class HttpAllowed:
    id = "cloudfront.http_allowed"
    severity = "medium"
    title = "CloudFront distribution allows plain HTTP"
    description = "A cache behaviour serves viewers over unencrypted HTTP instead of redirecting to HTTPS."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in _distributions(snap):
            if r.attrs.get("viewer_protocol") == "allow-all":
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


# CloudFront's security policies that still accept SSLv3, TLS 1.0 or TLS 1.1. Listing the weak ones (rather than
# "anything not TLSv1.2*") keeps newer, stronger policies such as TLSv1.3_2025 from being reported. A param, so an
# operator can extend it without editing the rule.
WEAK_TLS_POLICIES = ["SSLv3", "TLSv1", "TLSv1_2016", "TLSv1.1_2016"]


@register_rule
class OldTls:
    id = "cloudfront.old_tls"
    severity = "high"
    title = "CloudFront distribution allows TLS older than 1.2"
    description = ("The custom certificate's security policy lets viewers negotiate TLS 1.0/1.1 or SSLv3. "
                   "Distributions on the default certificate are not reported: AWS sets that floor.")
    params = {"weak_policies": WEAK_TLS_POLICIES}

    def evaluate(self, snap, params):
        weak = set(params["weak_policies"])
        for r in _distributions(snap):
            tls = r.attrs.get("min_tls")
            if r.attrs.get("custom_certificate") and tls in weak:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity, {"min_tls": tls})


@register_rule
class NoWaf:
    id = "cloudfront.no_waf"
    severity = "low"
    title = "CloudFront distribution has no WAF web ACL"
    description = "An enabled distribution isn't protected by AWS WAF."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in _distributions(snap):
            if r.attrs.get("enabled") and r.attrs.get("waf") is False:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)


@register_rule
class NoAccessLogging:
    id = "cloudfront.no_access_logging"
    severity = "low"
    title = "CloudFront distribution has no access logging"
    description = "Standard access logs are off, so viewer requests can't be investigated later."
    params: dict = {}

    def evaluate(self, snap, params):
        for r in _distributions(snap):
            if r.attrs.get("access_logging") is False:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity)
