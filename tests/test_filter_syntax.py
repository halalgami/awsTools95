from decimal import Decimal

import pytest

from aws95.kit.widgets.filter_syntax import FilterError, haystack, parse

ROWS = [
    {"name": "web-01", "state": "running", "region": "us-east-1", "cpu": 4, "cost": Decimal("12.50")},
    {"name": "batch-worker", "state": "stopped", "region": "us-east-1", "cpu": 8, "cost": Decimal("80.00")},
    {"name": "bastion", "state": "running", "region": "eu-west-1", "cpu": 2, "cost": None},
    {"name": "etl node", "state": None, "region": "me-south-1", "cpu": "n/a", "cost": Decimal("3")},
]
FIELDS = {"name", "state", "region", "cpu", "cost"}


def names(text):
    q = parse(text, FIELDS)
    return [r["name"] for r in ROWS if q.matches(r)]


@pytest.mark.parametrize("text,expected", [
    ("", ["web-01", "batch-worker", "bastion", "etl node"]),
    ("state:running", ["web-01", "bastion"]),
    ("STATE:Running", ["web-01", "bastion"]),
    ("region:us-*", ["web-01", "batch-worker"]),
    ("name~work", ["batch-worker"]),
    ("-state:running", ["batch-worker", "etl node"]),
    ("cpu>=4", ["web-01", "batch-worker"]),
    ("cost<20", ["web-01", "etl node"]),
    ("bast", ["bastion"]),
    ('"etl node"', ["etl node"]),
    ("state:running region:eu-*", ["bastion"]),
    ("-temp", ["web-01", "batch-worker", "bastion", "etl node"]),
])
def test_filter_cases(text, expected):
    assert names(text) == expected


def test_unknown_field_prefix_is_a_bare_word():
    rows = [{"arn": "arn:aws:s3:::logs"}, {"arn": "arn:aws:s3:::site"}]
    q = parse("arn:aws:s3:::logs", fields={"name"})
    assert [r for r in rows if q.matches(r)] == [rows[0]]


@pytest.mark.parametrize("bad", ["cost>abc", 'name:"web', "cpu<=", "cpu>"])
def test_malformed_input_raises_filter_error(bad):
    with pytest.raises(FilterError):
        parse(bad, FIELDS)


def test_needs_haystack_only_for_bare_words():
    assert not parse("state:running", FIELDS).needs_haystack
    assert parse("web", FIELDS).needs_haystack


def test_haystack_skips_none_and_lowercases():
    assert haystack({"a": "Web", "b": None, "c": 3}) == "web 3"


def test_empty_query():
    q = parse("   ")
    assert q.is_empty and q.matches({"x": 1})


@pytest.mark.parametrize("text", ["name:[", "name:[a", "name:*[", "name:a[]", "name:[!", "name:]"])
def test_bad_glob_never_crashes(text):
    q = parse(text, FIELDS)
    for r in ROWS:
        assert q.matches(r) in (True, False)


@pytest.mark.parametrize("text", ["cost>nan", "cost<inf", "cost>=Infinity"])
def test_non_finite_numbers_raise_filter_error(text):
    with pytest.raises(FilterError):
        parse(text, FIELDS)


def test_nan_row_value_does_not_crash():
    q = parse("cost>1", FIELDS)
    assert not q.matches({"cost": "nan"})
    assert not q.matches({"cost": float("inf")})


def test_glob_with_bracket_class_matches():
    q = parse("name:web-0[12]", FIELDS)
    assert q.matches({"name": "web-01"}) and not q.matches({"name": "web-03"})
