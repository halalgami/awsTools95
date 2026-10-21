# Upstream: labsnap/labsnap/collect/runner.py @ 1b1d437 — rewritten as aws95's SnapshotService; owned here.
"""SnapshotService (first-drop spec §3.4). Qt-free; runs on the TaskRunner.

One snapshot at a time. Accounts run in sequence; within an account, sections (one collector in one region) run
MAX_PARALLEL at a time, each with its own time limit, and each section's data and state are saved the moment it ends
(resumable-snapshots spec §5). Unfinished sections stay in the store, so resume() continues the same snapshot. AWS text
never reaches the store: errors are classify()'d."""
from __future__ import annotations

import concurrent.futures as cf
import logging
import queue
import threading
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial

from aws95 import __version__
from aws95.aws import identity
from aws95.aws.engines.labsnap.analyze.findings import evaluate_snapshot
from aws95.aws.errors import classify
from aws95.aws.session import session_for
from aws95.contracts.accounts import RosterEntry
from aws95.contracts.common import Status
from aws95.contracts.snapshots import (NOTHING_TO_RESUME, RESUME_WINDOW, TOO_OLD, AccountRun, SnapshotOutcome,
                                       SnapshotProgress)
from aws95.core.cancel import AnyToken, CancelledError, CancelToken
from aws95.core.jobs import JobLock
from aws95.store import Store, parse_ts

log = logging.getLogger("aws95.snapshot")
MAX_PARALLEL = 4
ProgressCallback = Callable[[SnapshotProgress], None]


class SnapshotBusy(Exception):
    """A snapshot is already running."""


@dataclass
class _Section:
    state: str
    error: str | None
    resources: list = field(default_factory=list)
    metrics: list = field(default_factory=list)
    denials: int = 0


DENIED_HINT = "most sections were denied; check its permissions (My Accounts ▸ Permissions)"


def section_summary(states: Sequence[str]) -> str:
    """One account's end-of-run line (resumable-snapshots spec §5.5)."""
    total = len(states)
    done = sum(s in ("done", "partial") for s in states)
    if total and done == total:
        return f"complete ({done}/{total} sections)"
    resumable = sum(s in ("waiting", "stopped", "timed_out", "failed") for s in states)
    denied = sum(s == "denied" for s in states)
    parts = [f"{done}/{total} sections done"]
    if resumable:
        parts.append(f"{resumable} to resume")
    if denied:
        parts.append(f"{denied} denied")
    text = " · ".join(parts)
    return f"{text} — {DENIED_HINT}" if total and denied * 2 >= total else text


def regions_for_collector(collector, regions: Sequence[str]) -> list[str]:
    """The one source of regions_requested: the diff compares it with regions_covered to tell "gone" from
    "not looked at", so it must equal exactly the sections planned."""
    return ["global"] if collector.scope == "global" else list(regions)


def _default_collectors() -> list:
    import aws95.aws.engines.labsnap.collect.collectors  # noqa: F401 - registers them
    from aws95.aws.engines.labsnap.collect.base import select
    return select(None)


def _tuned_rules(store) -> list:
    """The shipped rules with the owner's local tuning (findings-tools spec §4)."""
    from aws95.aws.engines.labsnap.rules.base import RuleOverrides, load_rules
    return load_rules(RuleOverrides.from_store(store))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _DaemonPool:
    """A minimal ThreadPoolExecutor stand-in with DAEMON worker threads. ThreadPoolExecutor's workers are joined by
    its interpreter-exit hook, so a worker abandoned inside a botocore call would still hold up quitting the app until
    botocore's own timeout (re-review Minor 4). Daemon workers never block process exit. Futures are real
    `concurrent.futures.Future`s, so `cf.wait` works unchanged."""

    def __init__(self, max_workers: int, name: str) -> None:
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._threads = [threading.Thread(target=self._work, name=f"{name}-{i}", daemon=True)
                         for i in range(max_workers)]
        for t in self._threads:
            t.start()

    def _work(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fut, fn, args = item
            if not fut.set_running_or_notify_cancel():
                continue                                   # cancelled while queued
            try:
                fut.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001 - delivered through the future
                fut.set_exception(exc)

    def submit(self, fn: Callable, *args) -> cf.Future:
        fut: cf.Future = cf.Future()
        self._q.put((fut, fn, args))
        return fut

    def shutdown(self, futures: Collection[cf.Future]) -> None:
        """Cancel what has not started, then let idle workers exit. Never waits for a running worker."""
        for fut in futures:
            fut.cancel()
        for _ in self._threads:
            self._q.put(None)


class SnapshotService:
    def __init__(self, store: Store, *, session_factory: Callable[[str], object] = session_for,
                 collectors: Callable[[], list] = _default_collectors, rules: Callable[[], list] | None = None,
                 clock: Callable[[], float] = time.monotonic, utcnow: Callable[[], datetime] = _utcnow,
                 max_parallel: int = MAX_PARALLEL, poll_s: float = 0.1, grace_s: float = 1.0,
                 jobs: JobLock | None = None) -> None:
        self._store = store
        self._session_for = session_factory
        self._collectors = collectors
        self._rules = rules or partial(_tuned_rules, store)
        self._clock = clock
        self._utcnow = utcnow
        self._max = max_parallel
        self._poll = poll_s
        self._grace = grace_s
        self._jobs = jobs or JobLock()                # one long job at a time: a snapshot or a backfill

    @property
    def running(self) -> bool:
        return self._jobs.holder == "snapshot"

    def take(self, accounts: Sequence[RosterEntry], include_billing_for: Collection[str],
             regions_for: Mapping[str, Sequence[str]], cancel: CancelToken | AnyToken | None = None,
             progress: ProgressCallback | None = None, labels: Mapping[str, str] | None = None) -> SnapshotOutcome:
        return self._each(accounts, cancel, labels, partial(self._account_take, include_billing_for, regions_for,
                                                             progress))

    def resume(self, accounts: Sequence[RosterEntry], include_billing_for: Collection[str],
               cancel: CancelToken | AnyToken | None = None, progress: ProgressCallback | None = None,
               labels: Mapping[str, str] | None = None, *, retry_denied: bool = False) -> SnapshotOutcome:
        return self._each(accounts, cancel, labels, partial(self._account_resume, include_billing_for, retry_denied,
                                                             progress))

    def _each(self, accounts, cancel, labels, run_one) -> SnapshotOutcome:
        if not self._jobs.try_acquire("snapshot"):
            raise SnapshotBusy("A snapshot is already running." if self._jobs.holder == "snapshot"
                               else self._jobs.busy_message())
        try:
            runs = []
            for entry in accounts:
                if cancel is not None and cancel.cancelled:
                    runs.append(AccountRun(entry.account_id, None, "failed", "Cancelled before it started."))
                    continue
                runs.append(run_one(entry, cancel, (labels or {}).get(entry.account_id) or entry.profile))
            return SnapshotOutcome(tuple(runs), bool(cancel is not None and cancel.cancelled))
        finally:
            self._jobs.release()

    def _identity(self, entry: RosterEntry, cancel) -> tuple[str, str] | AccountRun:
        try:
            session = self._session_for(entry.profile)
            account, arn = identity.caller(session, cancel)
        except CancelledError:
            return AccountRun(entry.account_id, None, "failed", "Cancelled before it started.")
        except Exception as exc:  # noqa: BLE001 - recorded, never raised
            return AccountRun(entry.account_id, None, "failed", classify(exc, "sts", "GetCallerIdentity").message)
        if account != entry.account_id:
            return AccountRun(entry.account_id, None, "failed",
                              f"The profile {entry.profile} now signs in to a different account. "
                              "Verify it in My Accounts. Nothing was saved.")
        return account, arn

    def _wanted(self, billing: bool) -> dict[str, object]:
        return {c.name: c for c in self._collectors() if billing or not getattr(c, "billed", False)}

    def _account_take(self, include_billing_for, regions_for, progress, entry: RosterEntry, cancel,
                      label: str) -> AccountRun:
        regions = tuple(regions_for.get(entry.account_id) or ())
        if not regions:
            return AccountRun(entry.account_id, None, "failed", "No regions chosen for this account.")
        who = self._identity(entry, cancel)
        if isinstance(who, AccountRun):
            return who
        account, arn = who
        collectors = list(self._wanted(entry.account_id in include_billing_for).values())
        observed_at = self._utcnow()
        sid = self._store.begin_snapshot(account, arn, __version__, regions, taken_at=observed_at)
        plan = [(c, r) for c in collectors for r in regions_for_collector(c, regions)]
        self._store.plan_sections(sid, [(c.name, r) for c, r in plan])
        return self._run(sid, entry, account, plan, 0, cancel, progress, label, observed_at)

    def _account_resume(self, include_billing_for, retry_denied: bool, progress, entry: RosterEntry, cancel,
                        label: str) -> AccountRun:
        snap = self._store.latest_snapshot(entry.account_id)
        if snap is None:
            return AccountRun(entry.account_id, None, "failed", "No snapshot to resume yet. Take one first.")
        sid = snap["id"]
        if self._utcnow() - parse_ts(snap["taken_at"]) > RESUME_WINDOW:
            return AccountRun(entry.account_id, sid, snap["status"], TOO_OLD)
        wanted = self._wanted(entry.account_id in include_billing_for)
        plan = [(wanted[c], r) for c, r in self._store.resumable_sections(sid, retry_denied=retry_denied)
                if c in wanted]
        if not plan:
            return AccountRun(entry.account_id, sid, snap["status"], NOTHING_TO_RESUME)
        who = self._identity(entry, cancel)
        if isinstance(who, AccountRun):
            return who
        done_before = len(self._store.sections(sid)) - len(plan)
        return self._run(sid, entry, who[0], plan, done_before, cancel, progress, label, self._utcnow())

    def _run(self, sid: int, entry: RosterEntry, account: str, plan: list, done_before: int, cancel,
             progress: ProgressCallback | None, label: str, observed_at: datetime) -> AccountRun:
        total = done_before + len(plan)
        done = [done_before]
        lock = threading.Lock()

        def report(collector: str | None, region: str | None) -> None:
            if progress is None:
                return
            with lock:
                finished = done[0]
            try:
                progress(SnapshotProgress(account, label, finished, total, region, collector))
            except Exception:  # noqa: BLE001 - a UI relay must never sink the run
                log.warning("snapshot progress callback failed")

        report(None, None)
        keys = [(c.name, r) for c, r in plan]
        collector_of = {(c.name, r): c for c, r in plan}
        tokens = {k: CancelToken() for k in keys}
        started: dict[tuple[str, str], float] = {}
        timed_out: set[tuple[str, str]] = set()
        give_up: dict[tuple[str, str], float] = {}
        denials = 0
        pool = _DaemonPool(self._max, "aws95-snapshot")
        futures: dict[cf.Future, tuple[str, str]] = {}

        def end(key: tuple[str, str], s: _Section) -> None:
            nonlocal denials
            self._store.end_section(sid, key[0], key[1], s.state, ended_at=self._utcnow(), resources=s.resources,
                                    metrics=s.metrics, observed_at=observed_at, denials=s.denials, error=s.error)
            denials += s.denials
            with lock:
                done[0] += 1
            report(None, None)

        try:
            futures = {pool.submit(self._run_section, collector_of[k], entry.profile, k, sid,
                                   AnyToken(cancel, tokens[k]), started, report): k for k in keys}
            pending = set(futures)
            while pending:
                finished, pending = cf.wait(pending, timeout=self._poll, return_when=cf.FIRST_COMPLETED)
                for fut in finished:
                    key = futures[fut]
                    try:
                        s = fut.result()
                    except cf.CancelledError:
                        continue                                    # never started: the section stays waiting
                    except Exception as exc:  # noqa: BLE001 - _run_section never raises; keeps a bug from losing it
                        s = _Section("failed", classify(exc).message)
                    if s is None:
                        continue                                    # picked up after Cancel: stays waiting
                    if s.state == "stopped" and key in timed_out:
                        s = _Section("timed_out", f"timed out after {collector_of[key].timeout_s}s")
                    end(key, s)
                now = self._clock()
                stopping = cancel is not None and cancel.cancelled
                for fut in list(pending):
                    key = futures[fut]
                    t0 = started.get(key)
                    if t0 is None:
                        if stopping:
                            fut.cancel()                            # queued: it stays waiting
                        continue
                    if key not in timed_out and now - t0 > collector_of[key].timeout_s:
                        timed_out.add(key)
                        tokens[key].cancel()
                    if stopping:
                        tokens[key].cancel()
                    if (stopping or key in timed_out) and key not in give_up:
                        give_up[key] = now + self._grace
                    if key in give_up and now >= give_up[key]:
                        pending.discard(fut)                        # abandoned: a late result is never saved
                        if key in timed_out:
                            end(key, _Section("timed_out", f"timed out after {collector_of[key].timeout_s}s"))
                        else:
                            end(key, _Section("stopped", "stopped before it finished"))
        finally:
            pool.shutdown(futures)

        status = self._store.finish_snapshot(sid)
        try:
            evaluate_snapshot(self._store, sid, self._rules())
        except Exception as exc:  # noqa: BLE001 - the snapshot is saved; findings can be re-derived from it
            log.warning("findings evaluation failed: %s", type(exc).__name__)
        summary = section_summary([r["state"] for r in self._store.sections(sid)])
        if cancel is not None and cancel.cancelled:
            summary = f"Cancelled. What finished was saved. {summary}"
        return AccountRun(account, sid, status, summary, denials)

    def _run_section(self, collector, profile: str, key: tuple[str, str], sid: int, token: AnyToken,
                     started: dict, report: Callable[[str | None, str | None], None]) -> _Section | None:
        if token.cancelled:
            return None                         # picked up after Cancel: it never started, so it stays waiting
        started[key] = self._clock()
        self._store.start_section(sid, key[0], key[1], self._utcnow())
        report(key[0], key[1])
        try:
            session = self._session_for(profile)       # one session per worker: boto3 sessions are not thread-safe
            res = collector.collect(session, key[1], cancel=token)
        except CancelledError:
            return _Section("stopped", "stopped before it finished")
        except Exception as exc:  # noqa: BLE001 - a section failure is recorded, never fatal
            c = classify(exc)
            return _Section("denied" if c.status is Status.DENIED else "failed", c.message)
        return _Section("partial" if res.denials else "done", None, list(res.resources), list(res.metrics),
                        res.denials)
