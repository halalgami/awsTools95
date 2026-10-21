import os
import stat
import sys
import threading

import pytest

from aws95.aws import sso
from aws95.core.cancel import CancelToken

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake CLI is a POSIX shell script")


def _fake_cli(tmp_path, monkeypatch, body: str):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "aws"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}/bin{os.pathsep}/usr/bin")
    monkeypatch.setattr(sso, "_EXTRA_CLI_PATHS", ())
    return script


def test_no_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(sso, "_EXTRA_CLI_PATHS", ())
    assert sso.aws_cli_path() is None
    assert sso.sign_in("dev", None) == (None, "no_cli")


def test_success_passes_profile_as_argv(tmp_path, monkeypatch):
    out = tmp_path / "args"
    _fake_cli(tmp_path, monkeypatch, f'echo "$@" > "{out}"; exit 0')
    assert sso.sign_in("acct:Role/x", None) == (0, "ok")
    assert out.read_text().strip() == "sso login --profile acct:Role/x"


def test_failure_exit_code(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, "exit 3")
    assert sso.sign_in("dev", None) == (3, "failed")


def test_timeout(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, "exec sleep 5")
    assert sso.sign_in("dev", None, timeout_s=1) == (None, "timeout")


def test_cancel_terminates(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, "exec sleep 5")
    t = CancelToken()
    threading.Timer(0.3, t.cancel).start()
    assert sso.sign_in("dev", t) == (None, "cancelled")


def test_prompt_relayed_url_then_code(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch,
              'echo "Attempting to open https://device.sso.us-east-1.amazonaws.com/ in browser"; '
              'echo "Then enter the code:"; echo "ABCD-EFGH"; echo "https://other.example/"; exit 0')
    seen = []
    assert sso.sign_in("dev", None, lambda url, code: seen.append((url, code))) == (0, "ok")
    assert seen == [("https://device.sso.us-east-1.amazonaws.com/", ""),
                    ("https://device.sso.us-east-1.amazonaws.com/", "ABCD-EFGH")]


def test_prompt_url_only_flow_relayed_once(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch, 'echo "Open https://auth.example/x?y=1"; echo "https://auth.example/x?y=1"; exit 0')
    seen = []
    assert sso.sign_in("dev", None, lambda url, code: seen.append((url, code))) == (0, "ok")
    assert seen == [("https://auth.example/x?y=1", "")]


def test_prompt_callback_exception_does_not_break_sign_in(tmp_path, monkeypatch):
    _fake_cli(tmp_path, monkeypatch,
              'echo "https://device.sso.us-east-1.amazonaws.com/"; echo "ABCD-EFGH"; echo more; exit 0')

    def boom(url, code):
        raise RuntimeError("ui gone")

    assert sso.sign_in("dev", None, boom) == (0, "ok")


@pytest.mark.parametrize("name", ["bad name", "-rf", "", "a;b", "x" * 200, "tab\there"])
def test_sign_in_rejects_bad_profile_name(tmp_path, monkeypatch, name):
    out = tmp_path / "ran"
    _fake_cli(tmp_path, monkeypatch, f'touch "{out}"')
    assert sso.sign_in(name, None) == (None, "bad_name")
    assert not out.exists()


def test_manual_command_quotes_names():
    assert sso.manual_command("dev") == "aws sso login --profile dev"
    assert sso.manual_command("quoted name") == "aws sso login --profile 'quoted name'"


def test_cmd_wrapper_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(sso.shutil, "which", lambda name: str(tmp_path / "aws.cmd"))
    monkeypatch.setattr(sso.sys, "platform", "win32")
    monkeypatch.setattr(sso, "_EXTRA_CLI_PATHS", ())
    assert sso.aws_cli_path() is None


def test_windows_path_walk_skips_relative_and_cmd(tmp_path, monkeypatch):
    absdir, other = tmp_path / "tools", tmp_path / "other"
    absdir.mkdir()
    other.mkdir()
    (absdir / "aws.cmd").write_text("x")
    (absdir / "aws.exe").write_text("x")
    cwd_bin = tmp_path / "cwd"
    cwd_bin.mkdir()
    (cwd_bin / "aws.exe").write_text("x")
    monkeypatch.chdir(cwd_bin)
    monkeypatch.setattr(sso.sys, "platform", "win32")
    monkeypatch.setattr(sso, "_EXTRA_CLI_PATHS", ())
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative", str(other), str(absdir)]))
    assert sso.aws_cli_path() == os.path.join(str(absdir), "aws.exe")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", str(absdir / "nope")]))
    assert sso.aws_cli_path() is None             # cwd copy is never used
