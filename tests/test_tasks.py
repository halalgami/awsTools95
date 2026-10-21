import threading

import pytest

from aws95.core.cancel import CancelledError, CancelToken, raise_if_cancelled
from aws95.core.tasks import CancelScope, TaskRunner


@pytest.fixture
def runner(qapp):
    r = TaskRunner(max_threads=4)
    yield r
    r.shutdown()


def test_cancel_token_basics():
    token = CancelToken()
    assert not token.cancelled
    raise_if_cancelled(token)
    raise_if_cancelled(None)
    token.cancel()
    assert token.cancelled
    with pytest.raises(CancelledError):
        raise_if_cancelled(token)


def test_result_delivered_on_gui_thread(runner, qtbot):
    seen = {}

    def on_finished(value):
        seen["value"] = value
        seen["main"] = threading.current_thread() is threading.main_thread()

    handle = runner.submit(lambda x: x * 2, 21)
    handle.finished.connect(on_finished)
    with qtbot.waitSignal(handle.finished, timeout=3000):
        pass
    assert seen == {"value": 42, "main": True}
    qtbot.waitUntil(lambda: runner.active_count == 0)


def test_failure_reports_message_and_traceback(runner, qtbot):
    handle = runner.submit(lambda: 1 / 0)
    with qtbot.waitSignal(handle.failed, timeout=3000) as blocker:
        pass
    message, tb = blocker.args
    assert message.startswith("ZeroDivisionError")
    assert "Traceback" in tb


def test_late_result_after_cancel_is_dropped(runner, qtbot):
    gate = threading.Event()
    calls = []
    handle = runner.submit(gate.wait, 5)
    handle.finished.connect(calls.append)
    handle.failed.connect(lambda *a: calls.append(a))
    handle.cancel()
    gate.set()
    qtbot.waitUntil(lambda: runner.active_count == 0, timeout=3000)
    assert calls == []
    assert handle.done


def test_kwargs_and_token_passthrough(runner, qtbot):
    token = CancelToken()

    def work(a, *, cancel):
        return (a, cancel is token)

    handle = runner.submit(work, 1, cancel=token)
    # submit() consumes cancel= itself and forwards the token because work() declares `cancel`.
    with qtbot.waitSignal(handle.finished, timeout=3000) as blocker:
        pass
    assert blocker.args == [(1, True)]


def test_scope_cancels_everything_it_tracks(runner, qtbot):
    scope = CancelScope()
    gate = threading.Event()
    calls = []
    for _ in range(3):
        h = scope.track(runner.submit(gate.wait, 5))
        h.finished.connect(calls.append)
    scope.cancel_all()
    gate.set()
    qtbot.waitUntil(lambda: runner.active_count == 0, timeout=3000)
    assert calls == []


def test_scope_drops_a_handle_when_it_settles(runner, qtbot):
    """A settled handle is released at once, not kept until the next track() (M3-R7)."""
    import gc
    import weakref

    scope = CancelScope()
    handle = scope.track(runner.submit(lambda: 42))
    ref = weakref.ref(handle)
    del handle
    qtbot.waitUntil(lambda: runner.active_count == 0, timeout=3000)
    qtbot.waitUntil(lambda: scope.pending == 0, timeout=1000)
    gc.collect()
    assert ref() is None


def test_active_changed_counts_up_and_down(runner, qtbot):
    counts = []
    runner.active_changed.connect(counts.append)
    handle = runner.submit(lambda: None)
    with qtbot.waitSignal(handle.settled, timeout=3000):
        pass
    assert counts[0] == 1 and counts[-1] == 0


def test_handles_and_runner_are_freed(qapp, qtbot):
    import gc
    import weakref

    r = TaskRunner(max_threads=4)
    handles = [r.submit(lambda: None) for _ in range(10)]
    refs = [weakref.ref(h) for h in handles]
    qtbot.waitUntil(lambda: r.active_count == 0, timeout=3000)
    del handles
    gc.collect()
    assert all(ref() is None for ref in refs)
    r.shutdown()
    runner_ref = weakref.ref(r)
    del r
    gc.collect()
    assert runner_ref() is None


def test_submit_from_other_thread_raises(runner):
    errors = []

    def attempt():
        try:
            runner.submit(lambda: None)
        except RuntimeError as exc:
            errors.append(str(exc))

    t = threading.Thread(target=attempt)
    t.start()
    t.join()
    assert errors == ["TaskRunner.submit must be called from the thread that owns the runner"]
    assert runner.active_count == 0


def test_base_exception_still_settles(runner, qtbot):
    def boom():
        raise SystemExit("bye")

    handle = runner.submit(boom)
    with qtbot.waitSignal(handle.failed, timeout=3000) as blocker:
        pass
    assert blocker.args[0].startswith("SystemExit")
    qtbot.waitUntil(lambda: runner.active_count == 0, timeout=3000)


def test_submit_does_not_parent_the_gui_thread_wrapper(qapp):
    """QObject.thread() hits shiboken's return-value heuristic: the one shared wrapper of the GUI thread's
    QThread becomes a Python child of the caller. Short-lived runners then churn that parent link, and
    the suite saw shiboken free the GUI QThread itself (segv-report.md). submit() must not call thread()."""
    import shiboken6
    from PySide6.QtCore import QThread

    runner = TaskRunner()
    try:
        runner.submit(int).cancel()
        assert "TaskRunner" not in shiboken6.dump(QThread.currentThread())
    finally:
        runner.shutdown()


def test_submit_after_shutdown_returns_dead_handle(qapp, qtbot):
    runner = TaskRunner(max_threads=1)
    runner.shutdown()
    ran, got = [], []
    h = runner.submit(ran.append, 1)
    h.finished.connect(got.append)
    qtbot.wait(50)
    assert runner.closed and h.token.cancelled and h.done
    assert ran == [] and got == [] and runner.dropped == 1


def test_shutdown_drops_queued_job(qapp, qtbot):
    runner = TaskRunner(max_threads=1)
    started, release, ran = threading.Event(), threading.Event(), []

    def block():
        started.set()
        release.wait(3)

    runner.submit(block)
    assert started.wait(3)
    runner.submit(ran.append, "queued")             # waits behind block() on the single thread
    threading.Timer(0.2, release.set).start()       # block() finishes only after shutdown() has cleared the queue
    assert runner.shutdown(timeout_ms=3000)
    qtbot.wait(50)
    assert ran == [] and runner.active_count == 0
