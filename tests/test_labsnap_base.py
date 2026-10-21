# Upstream: labsnap/tests/test_collect_base.py @ 1b1d437 — adapted for aws95; owned here from now on.
import pytest
from aws95.aws.engines.labsnap.collect import base
from aws95.aws.engines.labsnap.model import CollectorResult

# The upstream `make` helper decorates a class before naming it; register() warns about that on purpose.
pytestmark = pytest.mark.filterwarnings("ignore:collector C has no `name`:UserWarning")


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    monkeypatch.setattr(base, "_REGISTRY", {})


def make(name, scope="regional"):
    @base.register
    class C:
        pass

    C.name = name
    C.scope = scope
    C.timeout_s = 30
    C.volatile_keys = frozenset()
    C.collect = lambda self, session, region: CollectorResult([], [], [region or "global"])
    base._REGISTRY[name] = C
    return C


def test_registered_collectors_are_discoverable():
    make("s3")
    assert "s3" in base.all_collectors()


def test_select_returns_only_named_collectors():
    make("s3"); make("iam")
    assert [c.name for c in base.select(["iam"])] == ["iam"]


def test_select_none_returns_everything():
    make("s3"); make("iam")
    assert len(base.select(None)) == 2


def test_select_unknown_name_is_an_error():
    make("s3")
    with pytest.raises(base.UnknownCollector, match="nope"):
        base.select(["nope"])


def test_register_warns_and_skips_a_nameless_collector():
    class Nameless:
        pass

    with pytest.warns(UserWarning, match="Nameless"):
        result = base.register(Nameless)

    assert result is Nameless
    assert "Nameless" not in base.all_collectors()
    assert all(c is not Nameless for c in base.all_collectors().values())
