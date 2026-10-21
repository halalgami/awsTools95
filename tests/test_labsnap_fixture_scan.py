import re
from pathlib import Path

from tests.placeholder_ids import NEEDLES, Needles, account_allowed, digest, org_name_hits

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted([*ROOT.glob("tests/test_labsnap_*.py"), *ROOT.glob("tests/test_ctwatch_*.py"),
                ROOT / "tests" / "ctwatch_fakes.py", ROOT / "tests" / "activity_seed.py",
                *(ROOT / "aws95" / "aws" / "engines").rglob("*.py"),
                ROOT / "tests" / "store_seed.py", ROOT / "tests" / "engine_helpers.py"])
ACCOUNT = re.compile(r"(?<!\d)\d{12}(?!\d)")
ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
TEST_NEEDLES = Needles(tokens=frozenset({digest("zebracorp")}),
                       bigrams=frozenset({(digest("zebra"), digest("bank"))}))


def _existing():
    return [f for f in FILES if f.is_file()]


def test_scan_covers_the_ported_files():
    assert len([f for f in _existing() if f.name.startswith("test_labsnap_collector_")]) == 22


def test_only_placeholder_account_ids():
    bad = [(f.name, m) for f in _existing() for m in ACCOUNT.findall(f.read_text()) if not account_allowed(m)]
    assert bad == []


def test_access_keys_are_documentation_examples_only():
    bad = [(f.name, k) for f in _existing() for k in ACCESS_KEY.findall(f.read_text()) if not k.endswith("EXAMPLE")]
    assert bad == []


def test_no_organisation_identifiers():
    bad = [(f.name, hit) for f in _existing() for hit in org_name_hits(f.read_text())]
    assert bad == []


def test_needle_matching_is_whole_token_and_bigram():
    assert org_name_hits("Paid by ZebraCorp.", TEST_NEEDLES) == ["ZebraCorp"]
    assert org_name_hits("zebra-bank and Zebra Bank", TEST_NEEDLES) == ["zebra bank", "Zebra Bank"]
    assert org_name_hits("zebracorporate zebrabank", TEST_NEEDLES) == []
    assert all(len(d) == 64 for d in NEEDLES.tokens)


def test_shared_list_has_the_test_placeholders():
    for value in ("123456789012", "210987654321", "111111111111", "222222222222", "112233445566", "000000123456"):
        assert account_allowed(value)
    assert not account_allowed("12345678901" + "3")      # built at run time: this file is scanned too
