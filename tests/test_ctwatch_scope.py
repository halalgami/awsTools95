from aws95.aws.engines.ctwatch.scope import LOOKUP_PASSES, SCOPE, TARGETED_PASSES, is_human, keep_imported


def rule(key):
    return next(r for r in SCOPE if r.key == key)


def d(name, source, ptype="IAMUser", *, read_only=True, error=None, role=None, **extra):
    ui = {"type": ptype, "userName": "alice"}
    if ptype == "AssumedRole":
        ui = {"type": "AssumedRole", "arn": f"arn:aws:sts::1:assumed-role/{role}/s",
              "sessionContext": {"sessionIssuer": {"userName": role}}}
    out = {"eventName": name, "eventSource": source, "readOnly": read_only, "userIdentity": ui, **extra}
    if error:
        out["errorCode"] = error
    return out


def test_one_table_with_unique_keys():
    keys = [r.key for r in SCOPE]
    assert len(keys) == len(set(keys))
    bulk = LOOKUP_PASSES[0]
    assert bulk.key == "bulk" and bulk.lookup == ("ReadOnly", "false") and bulk.window_hours == 4
    assert len(LOOKUP_PASSES) == 14 and len(TARGETED_PASSES) == 13
    assert all(r.window_hours == 24 for r in TARGETED_PASSES)
    assert rule("root").lookup == ("Username", "root")
    assert {r.lookup[1] for r in TARGETED_PASSES if r.lookup[0] == "EventName"} == {
        "AssumeRole", "AssumeRoleWithSAML", "AssumeRoleWithWebIdentity", "GetSecretValue", "GetParameter",
        "GetParameters", "Decrypt", "GetSessionToken", "GetFederationToken", "ListAccessKeys", "GetCredentialReport",
        "ConsoleLogin"}
    assert {r.key for r in SCOPE if r.lookup is None} == {"denied", "insights"}


def test_sensitive_reads_are_human_only():
    keep = rule("name:GetParameter").keep
    assert keep(d("GetParameter", "ssm.amazonaws.com"))
    assert keep(d("GetParameter", "ssm.amazonaws.com", "AssumedRole", role="AWSReservedSSO_Dev_1"))
    assert not keep(d("GetParameter", "ssm.amazonaws.com", "AssumedRole", role="lambda-exec"))
    assert not keep(d("GetParameter", "ssm.amazonaws.com", "AWSService"))
    assert not rule("name:Decrypt").keep(d("Decrypt", "s3.amazonaws.com"))      # only kms:Decrypt
    assert rule("name:Decrypt").keep(d("Decrypt", "kms.amazonaws.com", "Root"))


def test_console_login_kept_including_failures():
    keep = rule("name:ConsoleLogin").keep
    assert keep(d("ConsoleLogin", "signin.amazonaws.com", read_only=False, errorMessage="Failed authentication"))
    assert keep(d("ConsoleLogin", "signin.amazonaws.com", "Unknown", read_only=False))


def test_root_reads_are_kept():
    root_read = d("ListBuckets", "s3.amazonaws.com", "Root")
    assert rule("root").keep(root_read) and keep_imported(root_read)


def test_import_keeps_denied_reads_and_insights_and_drops_plain_service_reads():
    assert keep_imported(d("GetObject", "s3.amazonaws.com", "AssumedRole", role="app", error="AccessDenied"))
    assert keep_imported({"eventType": "AwsCloudTrailInsight", "eventName": "AttachUserPolicy"})
    assert not keep_imported(d("GetObject", "s3.amazonaws.com", "AssumedRole", role="app"))
    assert keep_imported(d("PutObject", "s3.amazonaws.com", "AssumedRole", role="app", read_only=False))
    no_flag = d("CreateUser", "iam.amazonaws.com")
    del no_flag["readOnly"]
    assert keep_imported(no_flag)


def test_federated_role_assumption_passes_match():
    """Review I7: these events carry SAMLUser / WebIdentityUser, which ctwatch's human filter never matched."""
    saml = rule("name:AssumeRoleWithSAML").keep
    web = rule("name:AssumeRoleWithWebIdentity").keep
    assert saml(d("AssumeRoleWithSAML", "sts.amazonaws.com", "SAMLUser"))
    assert web(d("AssumeRoleWithWebIdentity", "sts.amazonaws.com", "WebIdentityUser"))
    assert not web(d("AssumeRoleWithWebIdentity", "sts.amazonaws.com", "AWSService"))
    assert not saml(d("AssumeRoleWithSAML", "sts.amazonaws.com", "WebIdentityUser"))
    assert not rule("name:AssumeRole").keep(d("AssumeRole", "sts.amazonaws.com", "WebIdentityUser"))


def test_is_human():
    assert is_human(d("x", "y", "SAMLUser")) and not is_human(d("x", "y", "WebIdentityUser"))
    assert is_human(d("x", "y", "Root")) and is_human(d("x", "y", "IAMUser"))
    assert is_human(d("x", "y", "AssumedRole", role="AWSReservedSSO_Admin_1"))
    assert not is_human(d("x", "y", "AssumedRole", role="ci")) and not is_human({})
