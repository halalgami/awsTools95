# Upstream: labsnap/labsnap/rules/builtin/elb.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule

# TLS 1.2 or better, no known-weak cipher suites. These are AWS's own
# predefined policy names; anything outside this set - including the
# long-standing default ELBSecurityPolicy-2016-08, which still negotiates
# TLS 1.0 - is reported. The set is a param so an operator can widen or
# narrow it without editing the rule.
APPROVED_TLS_POLICIES = [
    "ELBSecurityPolicy-TLS13-1-2-2021-06",
    "ELBSecurityPolicy-TLS13-1-2-Res-2021-06",
    "ELBSecurityPolicy-TLS13-1-3-2021-06",
    "ELBSecurityPolicy-TLS-1-2-2017-01",
    "ELBSecurityPolicy-TLS-1-2-Ext-2018-06",
    "ELBSecurityPolicy-FS-1-2-2019-08",
    "ELBSecurityPolicy-FS-1-2-Res-2019-08",
    "ELBSecurityPolicy-FS-1-2-Res-2020-10",
]

# Protocols that carry application traffic with no transport encryption.
# NLB TCP/UDP/TCP_UDP listeners are included by default because most of the
# time they carry plaintext application traffic directly - but a TCP
# listener can also be TLS passthrough (the NLB forwards the encrypted
# bytes untouched and a backend terminates TLS), in which case this default
# over-fires. See ListenerPlaintext.params below for how to narrow it.
PLAINTEXT_PROTOCOLS = {"HTTP", "TCP", "UDP", "TCP_UDP"}


@register_rule
class ListenerPlaintext:
    id = "elb.listener_plaintext"
    severity = "high"
    title = "Internet-facing load balancer accepts unencrypted traffic"
    description = ("A listener on a load balancer reachable from the internet "
                   "terminates a protocol with no transport encryption, so "
                   "credentials and payloads cross the public network in the "
                   "clear. Caveat: an internet-facing NLB TCP listener doing "
                   "TLS passthrough (the NLB forwards encrypted bytes "
                   "untouched; a backend terminates TLS) is not actually "
                   "plaintext - narrow the `plaintext_protocols` param to "
                   "exclude TCP/UDP/TCP_UDP if that is how this account uses "
                   "NLBs.")
    params: dict = {"plaintext_protocols": sorted(PLAINTEXT_PROTOCOLS)}

    def evaluate(self, snap, params):
        plaintext_protocols = set(
            params.get("plaintext_protocols", PLAINTEXT_PROTOCOLS))
        for r in snap.resources("elb.load_balancer"):
            if "scheme" not in r.attrs or "listeners" not in r.attrs:
                continue
            if r.attrs["scheme"] != "internet-facing":
                continue
            ports = [ln["port"] for ln in r.attrs["listeners"]
                     if ln.get("protocol") in plaintext_protocols]
            if ports:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"ports": ports})


@register_rule
class WeakTlsPolicy:
    id = "elb.weak_tls_policy"
    severity = "high"
    title = "Load balancer listener uses a TLS policy below the approved set"
    description = ("The listener negotiates with a predefined security policy "
                   "that is not in the approved list, which in AWS's older "
                   "policies means TLS 1.0 and 1.1 remain enabled.")
    params: dict = {"approved": APPROVED_TLS_POLICIES}

    def evaluate(self, snap, params):
        approved = set(params.get("approved", APPROVED_TLS_POLICIES))
        for r in snap.resources("elb.load_balancer"):
            if "listeners" not in r.attrs:
                continue
            bad = sorted({ln["ssl_policy"] for ln in r.attrs["listeners"]
                          if ln.get("ssl_policy")
                          and ln["ssl_policy"] not in approved})
            if bad:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"policies": bad})


@register_rule
class InternetFacing:
    id = "elb.internet_facing"
    severity = "medium"
    title = "Load balancer is reachable from the internet"
    description = ("The load balancer has public addresses. This is often "
                   "intended; it is reported so that what is exposed is a "
                   "recorded decision rather than an assumption. Mute it per "
                   "load balancer once reviewed.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("elb.load_balancer"):
            if "scheme" not in r.attrs:
                continue
            if r.attrs["scheme"] == "internet-facing":
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"lb_type": r.attrs.get("lb_type")})


@register_rule
class NoTargets:
    id = "elb.no_targets"
    severity = "low"
    title = "Target group has no registered targets"
    description = ("The target group is attached to a listener but routes to "
                   "nothing, so requests reaching it fail. Usually a leftover "
                   "from a decommissioned service.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("elb.target_group"):
            if "total_target_count" not in r.attrs:
                continue
            if r.attrs["total_target_count"] == 0:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"attached_lb_count":
                               r.attrs.get("attached_lb_count")})
