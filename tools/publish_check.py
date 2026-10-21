"""Pre-publish leak check for the public repository (spec §6.3).

Run: `uv run python -m tools.publish_check [--root PATH] [--commit REF]`. Exit 0 and print "publish_check: OK"
when clean; exit 1 and print one `path:line: rule: value` line per finding otherwise.

Design (every allowance is explicit, so a real identifier of the same shape still fails):
- account-id: any 12-digit run that is not part of a longer alphanumeric run, a decimal fraction or a UUID.
  Allowed: ALLOWED_ACCOUNT_IDS (AWS's documented placeholder and obviously patterned test dummies) and the
  labsnap fixture-scrub form (six leading zeros). Anything else, timestamps included, needs an entry in
  ALLOWED_OCCURRENCES keyed by (path, rule, value) with a written reason.
- arn-account: an ARN whose account field is a 12-digit ID that is not allowed (reported in addition to account-id).
- email: allowed only noreply@anthropic.com and RFC 2606 reserved domains (example.com/.org/.net, *.example,
  *.invalid, *.test, *.localhost). Commit-metadata mode also allows GitHub noreply addresses.
- access-key: AKIA/ASIA + 16 [A-Z0-9]; allowed only when it ends with EXAMPLE (AWS documentation convention).
- secret-key: a 40-char key value assigned to a secret-key name; allowed only when it contains EXAMPLE.
- org-name: alphanumeric tokens (case-insensitive) whose SHA-256 is in NEEDLES. The checker stores digests,
  not the names, so publishing the checker does not publish the names it guards against. Bucket names are
  caught through their distinctive token.
- data-file: a tracked file that .gitignore ignores, or that matches DATA_FILE_GLOBS.
PNG files are scanned through their text chunks (tEXt/zTXt/iTXt); other binary files (NUL in the first 8 KiB)
are skipped. EXCLUDED_DIRS are not part of the public tree and are not scanned.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import struct
import subprocess
import zlib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

EXCLUDED_DIRS: tuple[str, ...] = ("docs/superpowers/plans/", "docs/superpowers/followups/")

ALLOWED_ACCOUNT_IDS: frozenset[str] = frozenset({
    "123456789012",     # AWS documentation placeholder; the scrub target
    "210987654321",     # reversed placeholder, a second account in tests
    "111122223333",     # AWS documentation style (labsnap tests)
    "112233445566",     # patterned dummy (labsnap tests)
    *(d * 12 for d in "0123456789"),   # 111111111111 etc.: demo fixtures and tests
})
SCRUB_MARKER_PREFIX = "000000"   # labsnap's fixture scrubber writes 000000 + 6 digits

# (posix path, rule, value) -> reason. Exact matches only; add an entry per documented false positive.
ALLOWED_OCCURRENCES: dict[tuple[str, str, str], str] = {}

ALLOWED_EMAILS: frozenset[str] = frozenset({"noreply@anthropic.com"})
RESERVED_EMAIL_DOMAINS: tuple[str, ...] = ("example.com", "example.org", "example.net")
RESERVED_TLDS: tuple[str, ...] = ("example", "invalid", "test", "localhost")
GITHUB_NOREPLY = re.compile(r"^(?:\d+\+)?[A-Za-z0-9-]+@users\.noreply\.github\.com$")

DATA_FILE_GLOBS: tuple[str, ...] = ("*.db", "*.sqlite", "*.sqlite3", "*_report.csv", "*_report_summary.md",
                                    "AWS_COST_*.md", ".env", ".env.*", "*.pem", "*.key", "credentials.json")


def digest(token: str) -> str:
    return hashlib.sha256(token.lower().encode()).hexdigest()


@dataclass(frozen=True)
class Needles:
    tokens: frozenset[str]                  # digests of single tokens
    bigrams: frozenset[tuple[str, str]]     # digest pairs of two consecutive tokens


NEEDLES = Needles(
    tokens=frozenset({
        "0ca95a6716edcdc852b01f4cb73b015847d32d053576a927d82f8e8cdb4381c5",
        "aa005b530ccba1c5336a831bdab16af8ad10c1107954386f831f0631f173c94e",
        "dd316276e7d45320bafd67720bd91bae3f6e83d16d070f90999e78b3ac5aee76",
        "8bc17f86c34608da00f4c8156412d903616a6ff4984607868be3115413712e0e",
        "a915db9400db3c23d1d009584214598fefeff885a694f68c39f33744872d7637",
        "b103fdddee30977797868cd74b3780d3a5b8d4716e78506fb8c47e79d5ad13fb",
        "740e9a833ecb1756ee681a454f5e5ffece696a6f3c51107c9264a1a0e10da396",
        "6de41fd94ff093a7f9cebdcaa97fcd664e2acb511717c145a361558f8445f7b5",
        "38341e9f206a73445884260b011cd2d2bdedbeb67a9117f2124092719821060c",
        "57ba2da75d6f55032dc73b458f7244cf8460fddeef0a4b5d8c93cbcf6b1205f7",
        "9af2921d3fd57fe886c9022d1fcc055d53a79e4032fa6137e397583884e1a5de",
    }),
    bigrams=frozenset({
        ("6351825030155836664876686dcff61b6a91254a0458537072a1b26ad151b5ce",
         "0582bd2c13fff71d7f40ef5586e3f4da05a3a61fe5ba9f0b4d06e99905ab83ea"),
        ("6611032577e5a7b3a146e7b2111117b71a2a7b0fa4ef2861353816730a4f4a02",
         "2ad29f65743a0524d916bfb3e24f5034c970b8daa7749699a88bd7096129fa09"),
    }),
)

_UUID = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                   r"(?![0-9A-Za-z])")
_ACCOUNT = re.compile(r"(?<![0-9A-Za-z.])\d{12}(?![0-9A-Za-z]|\.\d)")
_ARN = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:(\d{12}):")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
_ACCESS_KEY = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_SECRET_KEY = re.compile(r"(?i)(?:secret_?access_?key|secret_?key)[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+]{40})"
                         r"(?![A-Za-z0-9/+=])")
_TOKEN = re.compile(r"[0-9A-Za-z]+")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    value: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.value}"


def account_allowed(value: str) -> bool:
    return value in ALLOWED_ACCOUNT_IDS or value.startswith(SCRUB_MARKER_PREFIX)


def org_name_hits(text: str, needles: Needles = NEEDLES) -> list[str]:
    """Whole tokens (case-insensitive) and consecutive-token pairs whose digests are needles (2b's fixture scan)."""
    tokens = [(m.group(0), digest(m.group(0))) for m in _TOKEN.finditer(text)]
    hits = [tok for tok, d in tokens if d in needles.tokens]
    first = {a for a, _ in needles.bigrams}
    hits += [f"{a} {b}" for (a, da), (b, db) in zip(tokens, tokens[1:]) if da in first and (da, db) in needles.bigrams]
    return hits


def _email_allowed(value: str, allow_github_noreply: bool) -> bool:
    address = value.lower()
    if address in ALLOWED_EMAILS:
        return True
    if allow_github_noreply and GITHUB_NOREPLY.match(value):
        return True
    domain = address.rsplit("@", 1)[1]
    if any(domain == d or domain.endswith("." + d) for d in RESERVED_EMAIL_DOMAINS):
        return True
    return domain.rsplit(".", 1)[-1] in RESERVED_TLDS


def scan_text(text: str, path: str = "<text>", *, needles: Needles = NEEDLES,
              allow_github_noreply: bool = False) -> list[Finding]:
    """Every finding in one text. Pure: no file system, no git."""
    findings: list[Finding] = []

    def add(start: int, rule: str, value: str) -> None:
        findings.append(Finding(path, text.count("\n", 0, start) + 1, rule, value))

    masked = _UUID.sub(lambda m: " " * len(m.group(0)), text)
    for m in _ACCOUNT.finditer(masked):
        if not account_allowed(m.group(0)):
            add(m.start(), "account-id", m.group(0))
    for m in _ARN.finditer(masked):
        if not account_allowed(m.group(1)):
            add(m.start(), "arn-account", m.group(0))
    for m in _EMAIL.finditer(text):
        if not _email_allowed(m.group(0), allow_github_noreply):
            add(m.start(), "email", m.group(0))
    for m in _ACCESS_KEY.finditer(text):
        if not m.group(0).endswith("EXAMPLE"):
            add(m.start(), "access-key", m.group(0))
    for m in _SECRET_KEY.finditer(text):
        if "EXAMPLE" not in m.group(1):
            add(m.start(1), "secret-key", m.group(1))
    tokens = [(m.start(), m.group(0), digest(m.group(0))) for m in _TOKEN.finditer(text)]
    for start, token, d in tokens:
        if d in needles.tokens:
            add(start, "org-name", token)
    first = {a for a, _ in needles.bigrams}
    for (start, token, d), (_, token2, d2) in zip(tokens, tokens[1:]):
        if d in first and (d, d2) in needles.bigrams:
            add(start, "org-name", f"{token} {token2}")
    return findings


def png_text(data: bytes) -> str:
    """The text chunks (tEXt, zTXt, iTXt) of a PNG, joined by newlines; '' when there are none."""
    out: list[str] = []
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        try:
            if kind == b"tEXt":
                out.append(body.replace(b"\0", b" ").decode("latin-1"))
            elif kind == b"zTXt":
                key, _, rest = body.partition(b"\0")
                out.append(key.decode("latin-1") + " " + zlib.decompress(rest[1:]).decode("latin-1"))
            elif kind == b"iTXt":
                key, _, rest = body.partition(b"\0")
                compressed, rest = rest[0], rest[2:]
                _lang, _, rest = rest.partition(b"\0")
                _tkey, _, value = rest.partition(b"\0")
                value = zlib.decompress(value) if compressed else value
                out.append(key.decode("latin-1") + " " + value.decode("utf-8", "replace"))
        except (zlib.error, IndexError):
            out.append(body.decode("latin-1"))
        pos += 12 + length
    return "\n".join(out)


def scan_bytes(data: bytes, path: str, *, needles: Needles = NEEDLES) -> list[Finding]:
    if path.lower().endswith(".png"):
        return scan_text(png_text(data), path, needles=needles)
    if b"\0" in data[:8192]:
        return []
    return scan_text(data.decode("utf-8", "replace"), path, needles=needles)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def tracked_files(root: Path) -> list[str]:
    """Tracked paths (posix), minus EXCLUDED_DIRS."""
    paths = [p for p in _git(root, "ls-files", "-z", "--cached").split("\0") if p]
    return sorted(p for p in paths if not p.startswith(EXCLUDED_DIRS))


def data_file_findings(root: Path, paths: list[str]) -> list[Finding]:
    ignored = {p for p in _git(root, "ls-files", "-z", "--cached", "--ignored", "--exclude-standard").split("\0") if p}
    out = []
    for p in paths:
        name = PurePosixPath(p).name
        if p in ignored or any(fnmatch(name, g) for g in DATA_FILE_GLOBS):
            out.append(Finding(p, 0, "data-file", name))
    return out


def scan_repo(root: Path, *, needles: Needles = NEEDLES) -> list[Finding]:
    """All findings in the tracked tree under `root`, minus ALLOWED_OCCURRENCES."""
    paths = tracked_files(root)
    findings = data_file_findings(root, paths)
    for p in paths:
        f = root / p
        if f.is_file():
            findings += scan_bytes(f.read_bytes(), p, needles=needles)
    kept = [f for f in findings if (f.path, f.rule, f.value) not in ALLOWED_OCCURRENCES]
    return sorted(kept, key=lambda f: (f.path, f.line, f.rule, f.value))


def scan_commit(root: Path, ref: str, *, needles: Needles = NEEDLES) -> list[Finding]:
    """Author, committer and message of one commit; GitHub noreply addresses are allowed here."""
    meta = _git(root, "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", ref)
    return scan_text(meta, f"commit {ref}", needles=needles, allow_github_noreply=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publish_check", description="Leak check before a public push.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--commit", metavar="REF", help="also check this commit's author, committer and message")
    args = parser.parse_args(argv)
    findings = scan_repo(args.root)
    if args.commit:
        findings += scan_commit(args.root, args.commit)
    for f in findings:
        print(f)
    if findings:
        print(f"publish_check: {len(findings)} finding(s)")
        return 1
    print("publish_check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
