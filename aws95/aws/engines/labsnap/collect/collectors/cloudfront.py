"""Amazon CloudFront distributions (edge-dns spec §3.3). Global: the API lives in us-east-1. Domain names, aliases
and origins are never stored; only their count and the security-relevant settings."""
from decimal import Decimal

from aws95.aws import gate
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource

# Weakest first: a distribution is as exposed as its weakest cache behaviour.
_STRENGTH = {"allow-all": 0, "redirect-to-https": 1, "https-only": 2}


@register
class CloudFrontCollector:
    name = "cloudfront"
    scope = "global"
    timeout_s = 180
    volatile_keys = frozenset()

    def collect(self, session, region, cancel=None):
        cf = gate.bind(session, "cloudfront", "us-east-1", cancel=cancel)
        resources, denials = [], 0
        for page in cf.pages("list_distributions"):
            for d in page.get("DistributionList", {}).get("Items", []) or []:
                behaviours = [d.get("DefaultCacheBehavior", {}), *(d.get("CacheBehaviors", {}).get("Items") or [])]
                policies = [b.get("ViewerProtocolPolicy") for b in behaviours if b.get("ViewerProtocolPolicy")]
                cert = d.get("ViewerCertificate", {})
                custom = not cert.get("CloudFrontDefaultCertificate", False)
                attrs = {"enabled": bool(d.get("Enabled")),
                         "viewer_protocol": min(policies, key=lambda p: _STRENGTH.get(p, 0)) if policies else None,
                         "custom_certificate": custom, "waf": bool(d.get("WebACLId")),
                         "price_class": d.get("PriceClass"), "alias_count": d.get("Aliases", {}).get("Quantity", 0)}
                if custom:
                    attrs["min_tls"] = cert.get("MinimumProtocolVersion")
                config, unreadable = probe(cf.get_distribution_config, Id=d["Id"])
                denials += unreadable
                if not unreadable and config is not None:
                    logging = config.get("DistributionConfig", {}).get("Logging", {})
                    attrs["access_logging"] = bool(logging.get("Enabled"))
                resources.append(Resource("cloudfront.distribution", "global", d["Id"], attrs, unreadable))
        return CollectorResult(resources, [Metric("cloudfront.distribution_count", Decimal(len(resources)))],
                               ["global"], denials)
