# Upstream: labsnap/labsnap/rules/builtin/dynamodb.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws.engines.labsnap.model import Finding
from aws95.aws.engines.labsnap.rules.base import register_rule

# There is deliberately no "dynamodb.no_encryption" rule. DynamoDB encrypts
# every table at rest; a table with no SSEDescription uses the AWS-owned
# default key. A rule on that state would be a permanent false positive on
# every default-configured table.


@register_rule
class PitrOff:
    id = "dynamodb.pitr_off"
    severity = "medium"
    title = "Table has no point-in-time recovery"
    description = ("Continuous backups are off, so there is no way to restore "
                   "the table to a moment before a bad write or a deletion. "
                   "Recovery depends entirely on whatever on-demand backups "
                   "happen to exist.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("dynamodb.table"):
            if "pitr_enabled" not in r.attrs:
                continue
            if not r.attrs["pitr_enabled"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"item_count": r.attrs.get("item_count")})


@register_rule
class NoDeletionProtection:
    id = "dynamodb.no_deletion_protection"
    severity = "low"
    title = "Table can be deleted without an extra step"
    description = ("Deletion protection is off, so a single API call or an "
                   "infrastructure change that drops the resource destroys the "
                   "table and its contents.")
    params: dict = {}

    def evaluate(self, snap, params):
        for r in snap.resources("dynamodb.table"):
            if "deletion_protection" not in r.attrs:
                continue
            if not r.attrs["deletion_protection"]:
                yield Finding(self.id, r.rtype, r.region, r.rid, self.severity,
                              {"item_count": r.attrs.get("item_count")})
