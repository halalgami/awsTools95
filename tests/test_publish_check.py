"""tools/publish_check: each rule catches a synthetic leak, each allowance is exact, and the tree is clean.

Synthetic leaks are assembled with j() so that no leak-shaped literal exists in this file: the tree scan
reads this file too.
"""
from __future__ import annotations

import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from tools import publish_check as pc

REPO = Path(__file__).resolve().parent.parent
LOCAL_NEEDLES = Path(__file__).resolve().parent / "publish_needles.local.txt"


def j(*parts: str) -> str:
    return "".join(parts)


FAKE_ID = j("314159", "265358")          # 12 digits, not a placeholder
TEST_NEEDLES = pc.Needles(tokens=frozenset({pc.digest("zebracorp")}),
                          bigrams=frozenset({(pc.digest("zebra"), pc.digest("bank"))}))


def rules(text: str, **kwargs) -> list[tuple[str, str]]:
    return [(f.rule, f.value) for f in pc.scan_text(text, **kwargs)]


# -- account-id / arn-account --------------------------------------------------------------------------

@pytest.mark.parametrize("template", ["account {} here", "id={}", "'{}'", "{}.", "x:{}:y", "-{}", "{}\n"])
def test_account_id_caught(template):
    assert rules(template.format(FAKE_ID)) == [("account-id", FAKE_ID)]


def test_account_id_line_number():
    [finding] = pc.scan_text(j("a\nb\n", FAKE_ID), "f.md")
    assert (finding.path, finding.line) == ("f.md", 3)


@pytest.mark.parametrize("template", ["{}7", "7{}", "abc{}def", "0.{}", "{}.5", "deadbeef-0000-4000-8000-{}"])
def test_account_id_shapes_that_are_not_ids(template):
    assert rules(template.format(FAKE_ID)) == []


@pytest.mark.parametrize("value", ["123456789012", "210987654321", "111122223333", "112233445566",
                                   "111111111111", "999999999999", "000000123456"])
def test_placeholder_account_ids_allowed(value):
    assert rules(f"acct {value} and arn:aws:iam::{value}:root") == []


def test_arn_with_real_looking_account_caught():
    arn = j("arn:aws:iam::", FAKE_ID, ":root")
    assert ("arn-account", j("arn:aws:iam::", FAKE_ID, ":")) in rules(arn)
    assert ("account-id", FAKE_ID) in rules(arn)


def test_regional_arn_caught():
    arn = j("arn:aws:lambda:me-south-1:", FAKE_ID, ":function:f")
    assert [r for r, _ in rules(arn)].count("arn-account") == 1


# -- email ---------------------------------------------------------------------------------------------

def test_email_caught():
    address = j("alice", "@", "corp.io")
    assert rules(f"mail {address}.") == [("email", address)]


def test_other_anthropic_address_caught():
    address = j("someone", "@", "anthropic.com")
    assert rules(address) == [("email", address)]


@pytest.mark.parametrize("address", ["noreply@anthropic.com", "bob@example.com", "x@sub.example.org",
                                     "a@example.net", "user-1a2b@example.invalid", "alice@realcorp.example",
                                     "t@host.test", "me@box.localhost"])
def test_reserved_emails_allowed(address):
    assert rules(f"<{address}>") == []


def test_github_noreply_allowed_only_in_commit_mode():
    address = j("1234+octo", "@", "users.noreply.github.com")
    assert rules(address) == [("email", address)]
    assert rules(address, allow_github_noreply=True) == []


# -- keys ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["AKIA", "ASIA"])
def test_access_key_caught(prefix):
    key = j(prefix, "ABCDEFGH", "23456789")
    assert rules(f"key={key}") == [("access-key", key)]


def test_access_key_doc_example_allowed_and_longer_runs_ignored():
    assert rules("AKIAIOSFODNN7EXAMPLE") == []
    assert rules(j("AKIA", "ABCDEFGH", "234567890")) == []      # 17 chars after the prefix: not a key id


@pytest.mark.parametrize("name", ["aws_secret_access_key = ", "SecretAccessKey: ", "\"secretKey\": \""])
def test_secret_key_caught(name):
    secret = j("abcdEFGH", "ijkl/MNO", "pqrs+TUV", "wxyz0123", "45678901")
    assert rules(j(name, secret)) == [("secret-key", secret)]


def test_secret_key_doc_example_and_short_test_values_allowed():
    assert rules("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY") == []
    assert rules("aws_secret_access_key = CONFIGSECRETVALUE") == []


# -- org-name (digests) --------------------------------------------------------------------------------

@pytest.mark.parametrize("text,value", [("ZebraCorp ltd", "ZebraCorp"), ("ZEBRACORP", "ZEBRACORP"),
                                        ("aws-cloudtrail-logs-zebracorp-datalake", "zebracorp"),
                                        ("Zebra Bank", "Zebra Bank"), ("zebra-bank", "zebra bank")])
def test_org_needles_caught(text, value):
    assert rules(text, needles=TEST_NEEDLES) == [("org-name", value)]


@pytest.mark.parametrize("text", ["zebracorporate", "zebra banking", "bank zebra", "zebra"])
def test_org_needles_match_whole_tokens_only(text):
    assert rules(text, needles=TEST_NEEDLES) == []


def test_shipped_needles_are_digests():
    for d in pc.NEEDLES.tokens | {x for pair in pc.NEEDLES.bigrams for x in pair}:
        assert len(d) == 64 and set(d) <= set("0123456789abcdef")


def test_shipped_needles_catch_the_local_list():
    """The gitignored local list holds the real identifiers; it exists on the owner's machine only."""
    if not LOCAL_NEEDLES.is_file():
        pytest.skip("tests/publish_needles.local.txt not present (only on the owner's machine)")
    lines = [line.strip() for line in LOCAL_NEEDLES.read_text().splitlines() if line.strip()]
    assert lines
    for line in lines:
        assert pc.scan_text(line), f"not caught: line {lines.index(line) + 1} of the local list"


def test_local_needles_file_is_gitignored():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    proc = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "tests/publish_needles.local.txt"])
    assert proc.returncode == 0, "add tests/publish_needles.local.txt to .gitignore"


# -- PNG and binary ------------------------------------------------------------------------------------

def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def _png(*chunks: bytes) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(chunks) + _chunk(b"IEND", b"")


@pytest.mark.parametrize("chunk", [
    lambda v: _chunk(b"tEXt", b"Comment\0" + v),
    lambda v: _chunk(b"zTXt", b"Comment\0\0" + zlib.compress(v)),
    lambda v: _chunk(b"iTXt", b"Comment\0\x01\0en\0\0" + zlib.compress(v)),
    lambda v: _chunk(b"iTXt", b"Comment\0\0\0\0\0" + v),
])
def test_png_text_chunks_scanned(chunk):
    data = _png(chunk(FAKE_ID.encode()))
    assert [(f.rule, f.value) for f in pc.scan_bytes(data, "shot.png")] == [("account-id", FAKE_ID)]


def test_png_without_text_and_binary_files_are_clean():
    assert pc.scan_bytes(_png(), "shot.png") == []
    assert pc.scan_bytes(b"RIFF\0\0" + FAKE_ID.encode(), "beep.wav") == []


# -- repository scan -----------------------------------------------------------------------------------

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    return tmp_path


def _add(repo: Path, rel: str, text: str, *, force: bool = False) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(repo, "add", *(["-f"] if force else []), rel)


def test_clean_repo_passes(repo, capsys):
    _add(repo, "a.py", "ACCOUNT = '123456789012'\n")
    assert pc.scan_repo(repo) == []
    assert pc.main(["--root", str(repo)]) == 0
    assert "publish_check: OK" in capsys.readouterr().out


def test_leak_in_repo_fails_with_location(repo, capsys):
    _add(repo, "docs/notes.md", j("intro\naccount ", FAKE_ID, "\n"))
    assert pc.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert j("docs/notes.md:2: account-id: ", FAKE_ID) in out and "1 finding(s)" in out


def test_untracked_files_are_not_scanned(repo):
    (repo / "scratch.md").write_text(FAKE_ID)
    assert pc.scan_repo(repo) == []


def test_excluded_dirs_not_scanned(repo):
    assert pc.EXCLUDED_DIRS == ("docs/superpowers/plans/", "docs/superpowers/followups/")
    _add(repo, "docs/superpowers/plans/p.md", FAKE_ID)
    _add(repo, "docs/superpowers/followups/f.md", FAKE_ID)
    _add(repo, "docs/superpowers/specs/s.md", FAKE_ID)
    assert [(f.path, f.rule) for f in pc.scan_repo(repo)] == [("docs/superpowers/specs/s.md", "account-id")]


def test_tracked_data_files_fail(repo):
    (repo / ".gitignore").write_text("*.db\n")
    _git(repo, "add", ".gitignore")
    _add(repo, "aws_data.db", "x", force=True)          # ignored by .gitignore, tracked anyway
    _add(repo, "AWS_COST_NOTES.md", "x")                # not ignored here, but a known data-file name
    _add(repo, "config/.env", "x")
    found = {(f.path, f.rule) for f in pc.scan_repo(repo)}
    assert found == {("aws_data.db", "data-file"), ("AWS_COST_NOTES.md", "data-file"), ("config/.env", "data-file")}


def test_allowed_occurrence_is_exact(repo, monkeypatch):
    _add(repo, "a.md", FAKE_ID)
    _add(repo, "b.md", FAKE_ID)
    monkeypatch.setitem(pc.ALLOWED_OCCURRENCES, ("a.md", "account-id", FAKE_ID), "documented timestamp")
    assert [f.path for f in pc.scan_repo(repo)] == ["b.md"]


def test_commit_metadata_checked(repo):
    _add(repo, "a.py", "x = 1\n")
    env_leak = {"GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": j("dev", "@", "corp.io"),
                "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": j("1+dev", "@", "users.noreply.github.com")}
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "-m",
                    "Initial\n\nCo-Authored-By: Claude <noreply@anthropic.com>"],
                   check=True, capture_output=True, env={**os.environ, **env_leak})
    assert [(f.rule, f.value) for f in pc.scan_commit(repo, "HEAD")] == [("email", j("dev", "@", "corp.io"))]


# -- one shared source with the 2b fixture scan ---------------------------------------------------------

def test_shared_lists_have_one_source():
    from tests import placeholder_ids as shared
    assert shared.NEEDLES is pc.NEEDLES and shared.ALLOWED_ACCOUNT_IDS is pc.ALLOWED_ACCOUNT_IDS
    assert shared.account_allowed is pc.account_allowed and shared.org_name_hits is pc.org_name_hits
    assert shared.digest is pc.digest and shared.Needles is pc.Needles
    assert shared.SCRUB_MARKER_PREFIX == pc.SCRUB_MARKER_PREFIX


def test_org_name_hits_semantics():
    assert pc.org_name_hits("Paid by ZebraCorp.", TEST_NEEDLES) == ["ZebraCorp"]
    assert pc.org_name_hits("zebra-bank and Zebra Bank", TEST_NEEDLES) == ["zebra bank", "Zebra Bank"]
    assert pc.org_name_hits("zebracorporate zebrabank", TEST_NEEDLES) == []
    assert pc.account_allowed("210987654321") and not pc.account_allowed(FAKE_ID)


# -- the real tree (Task 2) ----------------------------------------------------------------------------

RETIRED_PHRASES = (j("regulated ", "bank work"), j("leaves ", "the bank"), j("bank", "-internal"),
                   j("the bank", "'s accounts"), j("a bank ", "account"))


def _require_checkout() -> None:
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")


def test_public_tree_is_clean():
    """Spec §6.3: the pre-push check passes on every commit (tracked files minus EXCLUDED_DIRS)."""
    _require_checkout()
    findings = pc.scan_repo(REPO)
    assert not findings, "\n".join(map(str, findings))


def test_retired_phrases_replaced():
    """Spec §6.1: sector wording is replaced, not just the names the checker knows."""
    _require_checkout()
    hits = []
    for rel in pc.tracked_files(REPO):
        path = REPO / rel
        if not path.is_file() or b"\0" in path.read_bytes()[:8192]:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        hits += [f"{rel}: {phrase}" for phrase in RETIRED_PHRASES if phrase in text]
    assert not hits, "\n".join(hits)


PRIVATE_POINTERS = ("superpowers/plans", "superpowers/followups", "MASTER-TODO")
DELETED_FILES = (j(".env", ".example"),)


def test_private_references_are_marked():
    """Review Minor 14: a published doc mentions the private folders only on a line that says they are private,
    and nothing points at a file 2p deleted."""
    _require_checkout()
    hits = []
    for rel in pc.tracked_files(REPO):
        path = REPO / rel
        if not path.is_file() or b"\0" in path.read_bytes()[:8192]:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rel.endswith(".md") and any(p in line for p in PRIVATE_POINTERS) and "private" not in line.lower():
                hits.append(f"{rel}:{n}: unmarked pointer to a private folder")
            if any(d in line for d in DELETED_FILES):
                hits.append(f"{rel}:{n}: points at a deleted file")
    assert not hits, "\n".join(hits)
