# Upstream: ctwatch/tests/test_rules.py @ 254ff31 — adapted for aws95; owned here from now on.
from aws95.aws.engines.ctwatch.rules import evaluate

def d(name, rp=None, src="iam.amazonaws.com"):
    return {"eventName": name, "eventSource": src, "requestParameters": rp or {}}

def test_attach_admin_is_high():
    lvl, rid = evaluate(d("AttachUserPolicy", {"policyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}))
    assert lvl == 3 and rid == "iam-escalation"

def test_attach_nonadmin_not_high():
    lvl, _ = evaluate(d("AttachUserPolicy", {"policyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess"}))
    assert lvl < 3

def test_create_access_key_high():
    assert evaluate(d("CreateAccessKey"))[0] == 3

def test_delete_user_pool_medium():
    assert evaluate(d("DeleteUserPool", src="cognito-idp.amazonaws.com")) == (2, "destructive")

def test_stop_logging_low():
    assert evaluate(d("StopLogging", src="cloudtrail.amazonaws.com")) == (1, "security-config")

def test_benign_zero():
    assert evaluate(d("GetUser")) == (0, None)

def test_star_star_policy_is_high():
    rp = {"policyDocument": '{"Statement":[{"Action":"*","Resource":"*","Effect":"Allow"}]}'}
    assert evaluate(d("PutRolePolicy", rp))[0] == 3


def sts(name, ui_type):
    return {"eventName": name, "eventSource": "sts.amazonaws.com",
            "requestParameters": {}, "userIdentity": {"type": ui_type}}


def test_root_assume_role_is_high():
    assert evaluate(sts("AssumeRole", "Root")) == (3, "iam-escalation")


def test_iam_user_assume_role_is_low():
    assert evaluate(sts("AssumeRole", "IAMUser")) == (1, "role-assumption")


def test_service_role_assume_role_is_not_flagged():
    # role-to-role chaining is normal automation; must stay silent or the
    # watchlist drowns in service traffic.
    assert evaluate(sts("AssumeRole", "AssumedRole")) == (0, None)
    assert evaluate(sts("AssumeRole", "AWSService")) == (0, None)


def test_federated_assume_variants_covered():
    assert evaluate(sts("AssumeRoleWithSAML", "Root"))[0] == 3
    assert evaluate(sts("AssumeRoleWithWebIdentity", "IAMUser")) == (1, "role-assumption")


def test_pass_role_still_high():
    assert evaluate(d("PassRole")) == (3, "iam-escalation")


def test_secret_read_is_flagged_low():
    ev = {"eventName": "GetSecretValue", "eventSource": "secretsmanager.amazonaws.com",
          "requestParameters": {}, "userIdentity": {"type": "IAMUser"}}
    assert evaluate(ev) == (1, "secret-access")
