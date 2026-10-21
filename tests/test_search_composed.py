from PySide6.QtCore import QSettings

from aws95.contracts.common import Scope, Status
from aws95.core.settings import Settings
from aws95.providers.composed import PER_KIND, ComposedSearch
from aws95.providers.demo import demo_provider_set
from aws95.providers.live import live_provider_set
from aws95.providers.live.search import like_pattern
from tests.store_seed import DEV, PINNED, ROSTER, seed_store


def live(tmp_path):
    store = seed_store(tmp_path / "aws95.db")
    settings = Settings(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    settings.set_roster(ROSTER)
    return store, live_provider_set(settings, store=store, clock=lambda: PINNED)


def hits(ps, text, **kw):
    r = ps.search.search(Scope(), text, **kw)
    assert r.status is Status.OK
    return r.data


def test_short_queries_find_nothing(tmp_path):
    store, ps = live(tmp_path)
    try:
        assert hits(ps, "") == () and hits(ps, " a ") == ()
    finally:
        store.close()


def test_each_kind_is_found_with_its_navigation(tmp_path):
    store, ps = live(tmp_path)
    try:
        r = {h.kind: h for h in hits(ps, "i-0aaa")}["resource"]
        assert r.target_program == "resources" and r.payload == {"rtype": "ec2.instance", "filter": "rid:i-0aaa"}
        f = next(h for h in hits(ps, "no_lifecycle") if h.kind == "finding")
        assert f.target_program == "findings" and f.payload["rule"] == "s3.no_lifecycle"
        assert f.payload["target"].startswith("s3.bucket:")
        assert not [h for h in hits(ps, "sg_open_admin") if h.kind == "finding"]       # muted: not open
        p = [h for h in hits(ps, "ali") if h.kind == "resource"]
        assert [h.title for h in p] == ["alice"]
    finally:
        store.close()


def test_like_wildcards_are_literal(tmp_path):
    """Review Focus 2."""
    assert like_pattern("50%_\\x'") == "%50\\%\\_\\\\x'%"
    store, ps = live(tmp_path)
    try:
        assert hits(ps, "%%") == () and hits(ps, "__") == () and hits(ps, "a'b") == ()
    finally:
        store.close()


def test_per_kind_cap_and_total_limit():
    ps = demo_provider_set()
    many = hits(ps, "-0")                                # demo ids ("i-0…", "vol-0…") match widely
    by = {}
    for h in many:
        by.setdefault(h.kind, []).append(h)
    assert all(len(v) <= PER_KIND for v in by.values())
    assert len(hits(ps, "-0", limit=3)) == 3


def test_demo_hits_are_marked_demo():
    r = demo_provider_set().search.search(Scope(), "prod")
    assert r.is_demo


def test_scope_limits_accounts(tmp_path):
    store, ps = live(tmp_path)
    try:
        r = ps.search.search(Scope(accounts=(DEV,)), "prod-data")
        assert [h for h in r.data if h.kind == "resource"] == []
    finally:
        store.close()


def test_failing_sources_add_nothing():
    class Down:
        def __getattr__(self, name):
            from aws95.contracts.common import Result
            return lambda *a, **k: Result.not_connected("nope")
    s = ComposedSearch(Down(), Down(), Down(), Down())
    r = s.search(Scope(), "anything")
    assert r.status is Status.OK and r.data == ()
