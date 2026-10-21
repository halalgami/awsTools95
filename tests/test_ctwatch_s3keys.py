from datetime import date

import pytest

from aws95.aws.engines.ctwatch.s3keys import day_prefix, get_check, list_check, location
from aws95.aws.gate import WriteBlockedError

A = "123456789012"
OWNER = "111111111111"
B = "example-cloudtrail-bucket"
ORG = "o-abcde12345"
LOC = location(B, "/logs/", A, owner=A)
ORG_LOC = location(B, "logs", A, org_id=ORG, owner=OWNER)
DAY = date(2026, 10, 1)
KEY = f"logs/AWSLogs/{A}/CloudTrail/us-east-1/2026/10/01/{A}_CloudTrail_us-east-1_20261001T0000Z_abc123.json.gz"
ORG_KEY = f"logs/AWSLogs/{ORG}/{A}/CloudTrail/eu-west-1/2026/10/01/{A}_CloudTrail_eu-west-1_20261001T0005Z_x.json.gz"
INSIGHT_KEY = (f"logs/AWSLogs/{A}/CloudTrail-Insight/us-east-1/2026/10/01/"
               f"{A}_CloudTrail-Insight_us-east-1_20261001T0000Z_y.json.gz")


def test_location_normalises_and_refuses():
    assert LOC.prefix == "logs" and LOC.root == "logs/AWSLogs/" and LOC.org_id is None
    assert location(B, None, A).root == "AWSLogs/"
    for bucket, prefix, acct in [("Bad_Bucket", "", A), (B, "../x", A), (B, "a/%2e%2e", A), (B, "a\\b", A),
                                 (B, "", "12345")]:
        with pytest.raises(ValueError):
            location(bucket, prefix, acct)
    with pytest.raises(ValueError):
        location(B, "", A, org_id="o-BAD")
    with pytest.raises(ValueError):
        location(B, "", A, owner="not-an-account")


def test_day_prefix_layouts():
    assert day_prefix(LOC, "us-east-1", DAY) == f"logs/AWSLogs/{A}/CloudTrail/us-east-1/2026/10/01/"
    assert day_prefix(ORG_LOC, "eu-west-1", DAY, insight=True) == \
        f"logs/AWSLogs/{ORG}/{A}/CloudTrail-Insight/eu-west-1/2026/10/01/"
    with pytest.raises(ValueError):
        day_prefix(LOC, "us-east-1/../x", DAY)


@pytest.mark.parametrize("loc,key", [(LOC, KEY), (ORG_LOC, ORG_KEY), (LOC, INSIGHT_KEY)])
def test_get_allows_this_accounts_log_files(loc, key):
    get_check(loc)({"Bucket": B, "Key": key})
    get_check(loc)({"Bucket": B, "Key": key, "Range": "bytes=0-0"})
    get_check(loc)({"Bucket": B, "Key": key, "ExpectedBucketOwner": loc.owner})


@pytest.mark.parametrize("loc,params", [
    (LOC, {"Bucket": "other-bucket", "Key": KEY}),                                       # other bucket
    (LOC, {"Bucket": B, "Key": KEY.replace("logs/", "other/", 1)}),                      # other prefix
    (LOC, {"Bucket": B, "Key": KEY.replace(f"AWSLogs/{A}", "AWSLogs/210987654321")}),    # other account
    (LOC, {"Bucket": B, "Key": KEY.replace(".json.gz", ".json")}),                       # other extension
    (LOC, {"Bucket": B, "Key": KEY.replace(".json.gz", ".json.gz.exe")}),
    (LOC, {"Bucket": B, "Key": f"logs/AWSLogs/{A}/CloudTrail/../../private/2026/10/01/x.json.gz"}),   # traversal
    (LOC, {"Bucket": B, "Key": f"logs/AWSLogs/{A}/CloudTrail/us-east-1/2026/10/01/..%2F..%2Fx.json.gz"}),
    (LOC, {"Bucket": B, "Key": f"logs/AWSLogs/{A}/CloudTrail/us-east-1/2026/10/01/sub/x.json.gz"}),  # deeper
    (LOC, {"Bucket": B, "Key": "/" + KEY}),                                              # leading slash
    (LOC, {"Bucket": B, "Key": KEY, "VersionId": "v1"}),                                 # extra parameter
    (LOC, {"Bucket": B, "Key": KEY, "Range": "bytes=0-"}),                               # malformed range
    (LOC, {"Bucket": B, "Key": KEY, "ExpectedBucketOwner": "210987654321"}),             # another owner
    (LOC, {"Bucket": B}),                                                                # no key
    (LOC, {"Bucket": B, "Key": ORG_KEY}),                                                # org folder, plain trail
    (ORG_LOC, {"Bucket": B, "Key": KEY}),                                                # org trail, no org folder
    (ORG_LOC, {"Bucket": B, "Key": ORG_KEY.replace(ORG, "o-zzzzz99999")}),               # another organisation
    (location(B, "logs", A), {"Bucket": B, "Key": KEY, "ExpectedBucketOwner": A}),        # owner unknown
])
def test_get_refuses_everything_else(loc, params):
    with pytest.raises(WriteBlockedError):
        get_check(loc)(params)


@pytest.mark.parametrize("loc,prefix", [(LOC, day_prefix(LOC, "us-east-1", DAY)),
                                        (ORG_LOC, day_prefix(ORG_LOC, "eu-west-1", DAY)),
                                        (LOC, day_prefix(LOC, "us-east-1", DAY, insight=True))])
def test_list_allows_day_folders(loc, prefix):
    list_check(loc)({"Bucket": B, "Prefix": prefix})
    list_check(loc)({"Bucket": B, "Prefix": prefix, "MaxKeys": 1})
    list_check(loc)({"Bucket": B, "Prefix": prefix, "ContinuationToken": "opaque-token"})
    list_check(loc)({"Bucket": B, "Prefix": prefix, "ExpectedBucketOwner": loc.owner})


@pytest.mark.parametrize("loc,params", [
    (LOC, {"Bucket": "other-bucket", "Prefix": f"logs/AWSLogs/{A}/CloudTrail/"}),
    (LOC, {"Bucket": B, "Prefix": ""}),
    (LOC, {"Bucket": B, "Prefix": "logs/"}),
    (LOC, {"Bucket": B, "Prefix": "logs/AWSLogs/"}),                                      # the bucket root: never
    (LOC, {"Bucket": B, "Prefix": "logs/AWSLogs/", "Delimiter": "/"}),                    # no folder discovery (D4)
    (ORG_LOC, {"Bucket": B, "Prefix": "logs/AWSLogs/", "Delimiter": "/"}),
    (LOC, {"Bucket": B, "Prefix": f"logs/AWSLogs/{A}/"}),
    (LOC, {"Bucket": B, "Prefix": "logs/AWSLogs/210987654321/CloudTrail/"}),
    (LOC, {"Bucket": B, "Prefix": f"logs/AWSLogs/{A}/CloudTrail/../../"}),
    (LOC, {"Bucket": B, "Prefix": f"logs/AWSLogs/{A}/CloudTrail/", "Delimiter": "/"}),
    (LOC, {"Bucket": B, "Prefix": f"logs/AWSLogs/{A}/CloudTrail/", "StartAfter": "zzz"}),
    (LOC, {"Bucket": B, "Prefix": f"logs/AWSLogs/{A}/CloudTrail/", "ExpectedBucketOwner": "210987654321"}),
    (ORG_LOC, {"Bucket": B, "Prefix": f"logs/AWSLogs/o-zzzzz99999/{A}/CloudTrail/"}),
    (LOC, {"Bucket": B, "Prefix": None}),
])
def test_list_refuses_everything_else(loc, params):
    with pytest.raises(WriteBlockedError):
        list_check(loc)(params)
