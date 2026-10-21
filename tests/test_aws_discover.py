import os
import sys

import pytest

from aws95.aws import discover

CONFIG = """\
[default]
region = eu-west-1

[profile dev]
region = me-south-1
aws_access_key_id = AKIDCONFIGSECRET
aws_secret_access_key = CONFIGSECRETVALUE

[profile corp]
sso_session = corp
sso_account_id = 111111111111
sso_role_name = ReadOnly

[sso-session corp]
sso_start_url = https://example.awsapps.com/start
sso_region = us-east-1

[profile legacy]
sso_start_url = https://old.awsapps.com/start
sso_region = us-east-1
sso_account_id = 222222222222
sso_role_name = ReadOnly

[profile hop]
role_arn = arn:aws:iam::333333333333:role/ReadOnly
source_profile = corp

[profile mfa]
role_arn = arn:aws:iam::444444444444:role/Admin
source_profile = dev
mfa_serial = arn:aws:iam::123456789012:mfa/tester

[profile via-mfa]
role_arn = arn:aws:iam::555555555555:role/ReadOnly
source_profile = mfa

[profile proc]
credential_process = /usr/local/bin/get-creds --secret PROCSECRET

[profile loop1]
role_arn = arn:aws:iam::666666666666:role/x
source_profile = loop2

[profile loop2]
role_arn = arn:aws:iam::777777777777:role/x
source_profile = loop1

[profile "quoted name"]
region = us-east-1

[profile unquoted name]
region = us-east-1
"""
CREDS = """\
[dev]
aws_access_key_id = AKIDCREDSECRET
aws_secret_access_key = CREDSSECRETVALUE

[keysonly]
aws_access_key_id = AKIDKEYSONLY
aws_secret_access_key = KEYSONLYSECRET
"""
SECRETS = ("AKIDCONFIGSECRET", "CONFIGSECRETVALUE", "AKIDCREDSECRET", "CREDSSECRETVALUE", "AKIDKEYSONLY",
           "KEYSONLYSECRET", "PROCSECRET", "get-creds", "mfa/tester")


@pytest.fixture
def aws_files(tmp_path, monkeypatch):
    cfg, cred = tmp_path / "config", tmp_path / "credentials"
    cfg.write_text(CONFIG)
    cred.write_text(CREDS)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(cred))
    return cfg, cred


def _by_name():
    return {p.name: p for p in discover.list_profiles()}


def test_paths_follow_env(aws_files):
    assert discover.config_paths() == aws_files


def test_paths_default_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_CONFIG_FILE")
    monkeypatch.delenv("AWS_SHARED_CREDENTIALS_FILE")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert discover.config_paths() == (tmp_path / ".aws" / "config", tmp_path / ".aws" / "credentials")


def test_kinds_and_regions(aws_files):
    got = {n: (p.kind, p.region) for n, p in _by_name().items()}
    assert got["default"] == ("profile", "eu-west-1")
    assert got["dev"] == ("profile", "me-south-1")
    assert got["corp"] == ("sso", None) and got["legacy"] == ("sso", None)
    assert got["hop"] == ("assume_role", None)
    assert got["keysonly"] == ("profile", None)


def test_order_config_then_credentials_only(aws_files):
    names = [p.name for p in discover.list_profiles()]
    assert names[:2] == ["default", "dev"] and names[-1] == "keysonly"


def test_credentials_only_profile_listed(aws_files):
    assert "keysonly" in _by_name()


def test_sign_in_profile_chain(aws_files):
    p = _by_name()
    assert p["corp"].sign_in_profile == "corp"
    assert p["hop"].sign_in_profile == "corp"
    assert p["dev"].sign_in_profile is None
    assert p["loop1"].sign_in_profile is None            # cycle-safe


def test_mfa_and_process_verify_modes(aws_files):
    p = _by_name()
    assert p["mfa"].verify == "never" and "MFA" in p["mfa"].issue
    assert p["via-mfa"].verify == "never"                # MFA anywhere in the source chain
    assert p["proc"].verify == "manual"
    assert p["dev"].verify == "auto" and p["corp"].verify == "auto"


def test_quoted_name_kept_unquoted_space_dropped(aws_files):
    names = set(_by_name())
    assert "quoted name" in names and "unquoted name" not in names     # botocore's own behaviour


def test_no_secret_reaches_a_contract(aws_files):
    blob = repr(discover.list_profiles()) + repr(discover.config_sources())
    for s in SECRETS:
        assert s not in blob


def test_sources_counts_and_missing(aws_files, tmp_path, monkeypatch):
    by_kind = {s.kind: s for s in discover.config_sources()}
    assert by_kind["config"].exists and by_kind["config"].profile_count == len(
        [n for n in _by_name() if n != "keysonly"])
    assert by_kind["credentials"].profile_count == 2
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "nope"))
    cred = {s.kind: s for s in discover.config_sources()}["credentials"]
    assert (cred.exists, cred.profile_count) == (False, 0)


def test_both_missing_lists_nothing():
    assert discover.list_profiles() == ()          # the hermetic fixture points both at missing files


def test_many_profiles(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.write_text("".join(f"[profile p{i}]\nregion = us-east-1\n" for i in range(300)))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(cfg))
    assert len(discover.list_profiles()) == 300


def test_malformed_config_raises(tmp_path, monkeypatch):
    bad = tmp_path / "config"
    bad.write_text("[profile x\nregion = us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(bad))
    with pytest.raises(discover.DiscoveryError) as e:
        discover.list_profiles()
    assert str(bad) in str(e.value)


def _walk(exc, seen=None):
    """Every object reachable through an exception's cause/context/kwargs/args."""
    seen = set() if seen is None else seen
    if exc is None or id(exc) in seen:
        return
    seen.add(id(exc))
    yield exc
    for nxt in (exc.__cause__, exc.__context__, *getattr(exc, "kwargs", {}).values(), *exc.args):
        if isinstance(nxt, BaseException):
            yield from _walk(nxt, seen)


def test_discovery_error_carries_no_secret(tmp_path, monkeypatch):
    bad = tmp_path / "config"
    bad.write_text("[profile x]\naws_secret_access_key LEAKSECRET123\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(bad))
    with pytest.raises(discover.DiscoveryError) as e:
        discover.list_profiles()
    assert e.value.__cause__ is None and e.value.__context__ is None
    for exc in _walk(e.value):
        assert "LEAKSECRET123" not in repr(exc) + str(getattr(exc, "kwargs", ""))


def test_directory_config_raises(tmp_path, monkeypatch):
    d = tmp_path / "config"
    d.mkdir()
    monkeypatch.setenv("AWS_CONFIG_FILE", str(d))
    with pytest.raises(discover.DiscoveryError):
        discover.list_profiles()
    with pytest.raises(discover.DiscoveryError):
        discover.config_sources()


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="POSIX permissions; root can read anything")
def test_unreadable_config_raises(tmp_path, monkeypatch):
    f = tmp_path / "config"
    f.write_text("[default]\nregion = us-east-1\n")
    f.chmod(0)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(f))
    try:
        with pytest.raises(discover.DiscoveryError):
            discover.list_profiles()
    finally:
        f.chmod(0o600)


def test_paths_expand_env_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AWS_CONFIG_FILE", "$HOME/cfg")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "~/cred")
    assert discover.config_paths() == (tmp_path / "cfg", tmp_path / "cred")


def test_chain_is_capped_at_five_profiles(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.write_text("[profile p0]\nsso_session = s\n[sso-session s]\nsso_start_url = https://x\n" + "".join(
        f"[profile p{i}]\nrole_arn = arn:aws:iam::1:role/r\nsource_profile = p{i - 1}\n" for i in range(1, 7)))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(cfg))
    got = {p.name: p.sign_in_profile for p in discover.list_profiles()}
    assert got["p4"] == "p0"          # chain p4..p0 is 5 profiles
    assert got["p5"] is None and got["p6"] is None


def _write(tmp_path, monkeypatch, cfg_text, cred_text):
    cfg, cred = tmp_path / "config", tmp_path / "credentials"
    cfg.write_text(cfg_text)
    cred.write_text(cred_text)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(cred))


def test_credentials_file_mfa_is_never(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "",
           "[m]\naws_access_key_id = AKIDX\nmfa_serial = arn:aws:iam::1:mfa/credfileMFA\nrole_arn = arn:aws:iam::2:role/x\n")
    p = _by_name()["m"]
    assert p.verify == "never" and "MFA" in p.issue
    assert "credfileMFA" not in repr(discover.list_profiles())


def test_credentials_file_credential_process_is_manual(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "", "[c]\ncredential_process = /bin/sleep 999\n")
    p = _by_name()["c"]
    assert p.verify == "manual"
    assert "sleep" not in repr(discover.list_profiles())


def test_credentials_file_role_chain(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "[profile corp]\nsso_session = s\n[sso-session s]\nsso_start_url = https://x\n",
           "[hop]\nrole_arn = arn:aws:iam::2:role/x\nsource_profile = corp\n"
           "[viamfa]\nrole_arn = arn:aws:iam::2:role/y\nsource_profile = m\n[m]\nmfa_serial = arn:aws:iam::1:mfa/u\n")
    p = _by_name()
    assert p["hop"].kind == "assume_role" and p["hop"].sign_in_profile == "corp" and p["hop"].verify == "auto"
    assert p["viamfa"].verify == "never"


def test_same_name_merge_config_region_credentials_mfa(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "[profile both]\nregion = eu-west-1\n",
           "[both]\nmfa_serial = arn:aws:iam::1:mfa/u\n")
    p = _by_name()["both"]
    assert p.verify == "never" and p.region == "eu-west-1"
