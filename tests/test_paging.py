from aws95.contracts.common import Coverage, Page, Result, Status
from aws95.core.cancel import CancelToken, CancelledError
from aws95.contracts.paging import fetch_all_pages


def _pages(*pages):
    def call(cursor):
        return pages[int(cursor or 0)]
    return call


def test_merges_items_status_and_coverage():
    call = _pages(Result(Status.OK, Page((1,), "1"), Coverage(("r1", "r2"), ("r1", "r2"), 0), is_demo=True),
                  Result(Status.PARTIAL, Page((2,), None), Coverage(("r2", "r3"), ("r2",), 3)))
    r = fetch_all_pages(call)
    assert r.status is Status.PARTIAL and r.data == (1, 2) and r.is_demo
    assert r.coverage == Coverage(("r1", "r2", "r3"), ("r1", "r2"), 3)


def test_stale_beats_ok_partial_beats_stale():
    call = _pages(Result(Status.PARTIAL, Page((1,), "1")), Result(Status.STALE, Page((2,), None)))
    assert fetch_all_pages(call).status is Status.PARTIAL


def test_unusable_first_page_returned_unchanged():
    bad = Result(Status.DENIED, None, message="no")
    assert fetch_all_pages(_pages(bad)) is bad


def test_unusable_later_page_is_partial_with_earlier_rows():      # decision D6: no longer all-or-nothing
    bad = Result(Status.DENIED, None, message="no")
    r = fetch_all_pages(_pages(Result(Status.OK, Page((1,), "1")), bad))
    assert r.status is Status.PARTIAL and r.data == (1,)


def test_repeated_cursor_is_error():
    r = fetch_all_pages(lambda c: Result(Status.OK, Page((1,), "same"), is_demo=True))
    assert r.status is Status.ERROR and r.is_demo and "repeated" in r.message


def test_limit_truncates_with_message():
    r = fetch_all_pages(lambda c: Result.ok(Page((1, 2, 3), str((int(c or 0)) + 1))), limit=5)
    assert r.data == (1, 2, 3, 1, 2) and r.message == "Showing the first 5 items."


def test_limit_not_hit_has_no_message():
    r = fetch_all_pages(_pages(Result.ok(Page((1, 2), None))), limit=2)
    assert r.data == (1, 2) and r.message is None


def test_cancel_between_pages():
    token = CancelToken()

    def call(cursor):
        token.cancel()
        return Result.ok(Page((1,), "1"))

    try:
        fetch_all_pages(call, cancel=token)
    except CancelledError:
        return
    raise AssertionError("expected cancellation")


def test_later_page_failure_keeps_rows_as_partial():
    from aws95.contracts.common import Coverage, Page, Result, Status
    from aws95.programs.common.paging import fetch_all_pages
    pages = {None: Result.ok(Page((1, 2), "c1"), coverage=Coverage(("us-east-1",), ("us-east-1",), 0)),
             "c1": Result(Status.DENIED, None, Coverage(("eu-west-1",), (), 0), message="no")}
    r = fetch_all_pages(lambda c: pages[c])
    assert r.status is Status.PARTIAL and r.data == (1, 2)
    assert r.message == "Showing 2 rows. Page 2 failed: no"
    assert r.coverage.denials == 1 and not r.coverage.complete


def test_first_page_failure_unchanged():
    from aws95.contracts.common import Result, Status
    from aws95.programs.common.paging import fetch_all_pages
    r = fetch_all_pages(lambda c: Result(Status.DENIED, None, message="no"))
    assert r.status is Status.DENIED and r.data is None


def test_old_path_still_works():
    from aws95.contracts import paging as new
    from aws95.programs.common import paging as old
    assert old.fetch_all_pages is new.fetch_all_pages and old.merge_coverage is new.merge_coverage
