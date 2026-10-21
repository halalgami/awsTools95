# Upstream: ctwatch/ctwatch/ingest/fetcher.py (refresh) @ 254ff31 — rewritten as aws95's ActivityService; owned here.
"""ActivityService (first-drop spec §5). Qt-free; runs on the TaskRunner. One Activity job at a time (a load or an
import). Events are written BEFORE the coverage that claims them, so a crash or cancel between the two only repeats
work on the next run; coverage never claims a window that was not stored, and never the last SETTLE (15 min),
which CloudTrail may still be publishing (review C3). AWS text never reaches the store or the
UI: errors are classify()'d. Tasks 5a and 5b add the Import History… half."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aws95.aws import identity
from aws95.aws.engines.ctwatch.estimate import calls_needed, minutes
from aws95.aws.engines.ctwatch.fetcher import SETTLE, RateLimiter, day_slices, gaps, lookup, newest_first, sample
from aws95.aws.engines.ctwatch.history import (BAD_LOCATION, DENIED_STOP, KMS_STOP, ORG_STOP, BadLogFile,
                                              OrgLookupDenied, check_access, find_trails, is_kms_error, list_keys,
                                              open_s3, read_records, resolve_location, scoped)
from aws95.aws.engines.ctwatch.normalize import normalize_record
from aws95.aws.engines.ctwatch.s3keys import TrailLocation, day_prefix
from aws95.aws.engines.ctwatch.scope import LOOKUP_PASSES, TARGETED_PASSES, keep_imported
from aws95.aws.errors import classify
from aws95.aws.session import session_for
from aws95.contracts.accounts import RosterEntry
from aws95.contracts.activity import (ALL_REGIONS, DEFAULT_HISTORY_DAYS, HISTORY_DAYS, AccessCheck, AccountLoad,
                                      HistoryEstimate, HistoryOutcome, HistoryProgress, HistoryRequest, ImportPlan,
                                      JobKind, TrailLookup)
from aws95.contracts.common import Status
from aws95.core.cancel import CancelledError, raise_if_cancelled
from aws95.store import Store

log = logging.getLogger("aws95.activity")
CALLS_PER_SECOND = 2.0
NO_HISTORY_UPDATE = "No activity history yet for this account. Use Load Activity History… first."
MISMATCH = ("The profile {profile} now signs in to a different account. Verify it in My Accounts. "
            "Nothing was saved.")
CANCELLED = "Cancelled. What finished was saved; Resume Loading continues it."
ProgressCallback = Callable[[HistoryProgress], None]
Floors = dict[tuple[str, str], datetime]


def days_key(account: str) -> str:
    return f"activity/{account}/days"


def regions_key(account: str) -> str:
    return f"activity/{account}/regions"


def saved_days(text: str | None) -> int | None:
    try:
        n = int(text or "")
    except ValueError:
        return None
    return n if 1 <= n <= 3650 else None


class ActivityBusy(Exception):
    """An Activity load or import is already running."""


class ImportDenied(Exception):
    """Reading the trail's files was refused: stop the import (the next files would be refused too)."""


def _rows(records: list[dict], account: str, region: str) -> tuple[list, int]:
    """In-scope records for this account, normalised (and redacted). Returns (rows, records naming another account)."""
    out, foreign = [], 0
    for detail in records:
        if not detail.get("eventID") or not detail.get("eventTime") or not keep_imported(detail):
            continue
        recipient = detail.get("recipientAccountId")
        if recipient is not None and str(recipient) != account:      # review Minor 4
            foreign += 1
            continue
        try:
            out.append(normalize_record(detail, account=account, region=region))
        except (KeyError, TypeError, ValueError):
            continue                       # one malformed record never sinks the file
    return out, foreign


def _stop_if_denied(exc: BaseException, service: str, operation: str) -> None:
    if is_kms_error(exc):
        raise ImportDenied(KMS_STOP) from None
    if classify(exc, service, operation).status is Status.DENIED:
        raise ImportDenied(DENIED_STOP) from None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ActivityService:
    def __init__(self, store: Store, *, session_factory: Callable[[str], Any] = session_for,
                 clock: Callable[[], datetime] = _utcnow, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] = time.sleep, calls_per_second: float = CALLS_PER_SECOND) -> None:
        self._store = store
        self._session_for = session_factory
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._rate = calls_per_second
        self._busy = threading.Lock()
        self._state = threading.Lock()
        self._s3 = threading.Lock()              # Task 5: gate.validator is process-global; one S3 user at a time
        self._job: JobKind | None = None
        self._progress: str | None = None

    # ----- state -----
    @property
    def running(self) -> JobKind | None:
        with self._state:
            return self._job

    @property
    def progress_text(self) -> str | None:
        with self._state:
            return self._progress

    def _begin(self, kind: JobKind) -> None:
        if not self._busy.acquire(blocking=False):
            doing = "importing" if self.running == "import" else "loading"
            raise ActivityBusy(f"Activity is already {doing}.")
        with self._state:
            self._job, self._progress = kind, None

    def _end(self) -> None:
        with self._state:
            self._job, self._progress = None, None
        self._busy.release()

    def _reporter(self, progress: ProgressCallback | None, kind: JobKind, label: str,
                  total: int) -> Callable[[int, str | None], None]:
        def report(done: int, region: str | None) -> None:
            p = HistoryProgress(kind, label, done, total, region)
            with self._state:
                self._progress = p.text
            if progress is None:
                return
            try:
                progress(p)
            except Exception:  # noqa: BLE001 - a UI relay must never sink the run
                log.warning("activity progress callback failed")
        return report

    def _session_checked(self, entry: RosterEntry, cancel) -> tuple[Any, str | None]:
        try:
            session = self._session_for(entry.profile)
            who, _arn = identity.caller(session, cancel)
        except CancelledError:
            return None, "Cancelled before it started."
        except Exception as exc:  # noqa: BLE001 - recorded, never raised
            return None, classify(exc, "sts", "GetCallerIdentity").message
        if who != entry.account_id:
            return None, MISMATCH.format(profile=entry.profile)
        return session, None

    # ----- estimate (spec §5.1) -----
    def estimate(self, request: HistoryRequest, *, cancel=None) -> tuple[HistoryEstimate, ...]:
        now = self._clock()
        targeted = len(TARGETED_PASSES)
        out: list[HistoryEstimate] = []
        totals = {d: 0 for d in HISTORY_DAYS}
        lower_bound, sampled = False, False
        for entry in request.accounts:
            regions = tuple(request.regions_for.get(entry.account_id) or ())
            try:
                session = self._session_for(entry.profile)
            except Exception as exc:  # noqa: BLE001
                message = classify(exc).message
                out += [HistoryEstimate(entry.account_id, r, {}, error=message) for r in regions]
                lower_bound = True
                continue
            for region in regions:
                raise_if_cancelled(cancel)
                try:
                    count, more = sample(session, region, now, cancel=cancel)
                except CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    out.append(HistoryEstimate(entry.account_id, region, {},
                                               error=classify(exc, "cloudtrail", "LookupEvents").message))
                    lower_bound = True
                    continue
                calls = {d: calls_needed(count, d, targeted) for d in HISTORY_DAYS}
                for d in HISTORY_DAYS:
                    totals[d] += calls[d]
                sampled = True
                lower_bound = lower_bound or more
                out.append(HistoryEstimate(entry.account_id, region, {d: minutes(c) for d, c in calls.items()},
                                           at_least=more))
        if sampled:
            out.append(HistoryEstimate("", ALL_REGIONS, {d: minutes(c) for d, c in totals.items()},
                                       at_least=lower_bound))
        return tuple(out)

    # ----- load / resume / update (spec §5.2) -----
    def load(self, request: HistoryRequest, *, cancel=None, progress: ProgressCallback | None = None) -> HistoryOutcome:
        self._begin("load")
        try:
            runs = []
            for entry in request.accounts:
                if cancel is not None and cancel.cancelled:
                    runs.append(AccountLoad(entry.account_id, "failed", 0, "Cancelled before it started."))
                    continue
                runs.append(self._load_account(entry, request, cancel, progress))
            self._store.prune_events(self._clock() - timedelta(days=request.retention_days))
            return HistoryOutcome("load", tuple(runs), bool(cancel is not None and cancel.cancelled))
        finally:
            self._end()

    def _floors(self, account: str, regions: tuple[str, ...], request: HistoryRequest, now: datetime) -> Floors:
        """Where each (region, pass) starts: the chosen range's start for a load, the newest coverage for an update."""
        if request.mode == "update":
            floors: Floors = {}
            for region in regions:
                for rule in LOOKUP_PASSES:
                    covered = self._store.coverage(account, region, rule.key)
                    if covered:
                        floors[(region, rule.key)] = max(c.end for c in covered)
            return floors
        days = request.days or saved_days(self._store.get_meta(days_key(account))) or DEFAULT_HISTORY_DAYS
        self._store.set_meta(days_key(account), str(days))
        self._store.set_meta(regions_key(account), ",".join(regions))
        start = now - timedelta(days=min(days, request.retention_days))
        return {(region, rule.key): start for region in regions for rule in LOOKUP_PASSES}

    def _load_account(self, entry: RosterEntry, request: HistoryRequest, cancel,
                      progress: ProgressCallback | None) -> AccountLoad:
        account = entry.account_id
        regions = tuple(request.regions_for.get(account) or ())
        if not regions:
            return AccountLoad(account, "failed", 0, "No regions chosen for this account.")
        session, problem = self._session_checked(entry, cancel)
        if session is None:
            return AccountLoad(account, "failed", 0, problem)
        now = self._clock()
        settled = now - SETTLE
        floors = self._floors(account, regions, request, now)
        if not floors:
            return AccountLoad(account, "failed", 0, NO_HISTORY_UPDATE)
        slices = day_slices(min(floors.values()), now)
        report = self._reporter(progress, "load", request.labels.get(account) or entry.profile, len(slices))
        limiter = RateLimiter(self._rate, self._monotonic, self._sleep)
        tally = [0]               # events stored so far; a failing window must not lose what came before it
        failed: dict[str, str] = {}
        report(0, None)
        for index, (lo, hi) in enumerate(slices):
            for region in regions:
                if region in failed:
                    continue
                try:
                    self._fill(session, account, region, floors, lo, hi, limiter, cancel, report, index, settled,
                               tally)
                except CancelledError:
                    return AccountLoad(account, "partial", tally[0], CANCELLED)
                except Exception as exc:  # noqa: BLE001 - the region's gaps stay open for Resume
                    failed[region] = f"{region}: {classify(exc, 'cloudtrail', 'LookupEvents').message}"
            report(index + 1, None)
        added = tally[0]
        if not failed:
            return AccountLoad(account, "complete", added)
        status = "failed" if len(failed) == len(regions) and not added else "partial"
        return AccountLoad(account, status, added, "; ".join(failed[r] for r in regions if r in failed))

    def _fill(self, session: Any, account: str, region: str, floors: Floors, lo: datetime, hi: datetime,
              limiter: RateLimiter, cancel, report: Callable[[int, str | None], None], index: int,
              settled: datetime, tally: list[int]) -> None:
        for rule in LOOKUP_PASSES:
            floor = floors.get((region, rule.key))
            if floor is None:
                continue
            start = max(lo, floor)
            if start >= hi:
                continue
            covered = [(c.start, c.end) for c in self._store.coverage(account, region, rule.key)]
            for ws, we in newest_first(gaps(covered, start, hi), rule.window_hours):
                raise_if_cancelled(cancel)
                report(index, region)
                rows = lookup(session, region, rule, ws, we, account=account, limiter=limiter, cancel=cancel)
                tally[0] += self._store.insert_events(rows)                      # events first …
                end = min(we, settled)                                           # … then the settled part of
                if end > ws:                                                     # the window (review C3)
                    self._store.record_coverage(account, region, rule.key, ws, end)

    # ----- Import History… (spec §5.4) -----
    def find_trails(self, entry: RosterEntry, regions: tuple[str, ...], *, cancel=None) -> TrailLookup:
        session, problem = self._session_checked(entry, cancel)
        if session is None:
            return TrailLookup(entry.account_id, (), problem)
        return find_trails(session, entry.account_id, regions, cancel=cancel)

    def check_import(self, plan: ImportPlan, *, cancel=None) -> AccessCheck:
        if not self._s3.acquire(blocking=False):
            return AccessCheck(False, "An import is running. Check again when it finishes.")
        try:
            try:
                session = self._session_for(plan.reader_profile)
            except Exception as exc:  # noqa: BLE001
                return AccessCheck(False, classify(exc).message)
            return check_access(session, plan, cancel=cancel)
        finally:
            self._s3.release()

    def import_history(self, plan: ImportPlan, *, cancel=None,
                       progress: ProgressCallback | None = None) -> HistoryOutcome:
        self._begin("import")
        try:
            if not self._s3.acquire(blocking=False):
                return HistoryOutcome("import", (AccountLoad(plan.account.account_id, "failed", 0,
                                                             "An access check is running. Try again."),))
            try:
                run = self._import(plan, cancel, progress)
            finally:
                self._s3.release()
            return HistoryOutcome("import", (run,), bool(cancel is not None and cancel.cancelled))
        finally:
            self._end()

    def _import(self, plan: ImportPlan, cancel, progress: ProgressCallback | None) -> AccountLoad:
        account = plan.account.account_id
        try:
            session = self._session_for(plan.reader_profile)
            loc = resolve_location(session, plan, cancel=cancel)
        except CancelledError:
            return AccountLoad(account, "failed", 0, "Cancelled before it started.")
        except OrgLookupDenied:
            return AccountLoad(account, "failed", 0, ORG_STOP)
        except ValueError:
            return AccountLoad(account, "failed", 0, BAD_LOCATION)
        except Exception as exc:  # noqa: BLE001
            return AccountLoad(account, "failed", 0, classify(exc, "organizations", "DescribeOrganization").message)
        s3 = open_s3(session, plan.trail.home_region, cancel=cancel)
        days = plan.days
        report = self._reporter(progress, "import", plan.label or plan.account.profile, len(days))
        kinds = (False, True) if plan.trail.insights else (False,)
        added, foreign, problems = 0, 0, []
        report(0, None)
        try:
            with scoped(loc):
                for index, day in enumerate(days):
                    for region in plan.regions:
                        raise_if_cancelled(cancel)
                        report(index, region)
                        got, skipped, ok = self._import_day(s3, loc, account, region, day, kinds, problems, cancel)
                        added, foreign = added + got, foreign + skipped
                        if ok:
                            start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
                            self._store.record_coverage(account, region, "import", start, start + timedelta(days=1))
                    report(index + 1, None)
        except CancelledError:
            return AccountLoad(account, "partial", added, "Cancelled. Days already imported were saved.")
        except ImportDenied as exc:
            return AccountLoad(account, "partial" if added else "failed", added, str(exc))
        notes = []
        if foreign:
            notes.append(f"{foreign} record(s) named another account and were skipped.")
        if problems:
            notes.append(f"{len(problems)} log file(s) or folders could not be read; those days aren't marked "
                         f"imported. First: {problems[0]}")
        return AccountLoad(account, "partial" if problems else "complete", added, " ".join(notes) or None)

    def _import_day(self, s3: Any, loc: TrailLocation, account: str, region: str, day: date,
                    kinds: tuple[bool, ...], problems: list[str], cancel) -> tuple[int, int, bool]:
        added, foreign, ok = 0, 0, True
        for insight in kinds:
            try:
                keys = list_keys(s3, loc, day_prefix(loc, region, day, insight=insight))
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _stop_if_denied(exc, "s3", "ListBucket")
                problems.append(f"{region} {day:%Y-%m-%d}: {classify(exc, 's3', 'ListBucket').message}")
                ok = False
                continue
            for key in keys:
                raise_if_cancelled(cancel)
                try:
                    records = read_records(s3, loc, key)
                except CancelledError:
                    raise
                except BadLogFile as exc:
                    problems.append(f"{region} {day:%Y-%m-%d}: a log file could not be read ({exc})")
                    ok = False
                    continue
                except Exception as exc:  # noqa: BLE001 - a validator refusal lands here too
                    _stop_if_denied(exc, "s3", "GetObject")
                    problems.append(f"{region} {day:%Y-%m-%d}: {classify(exc, 's3', 'GetObject').message}")
                    ok = False
                    continue
                rows, skipped = _rows(records, account, region)
                foreign += skipped
                added += self._store.insert_events(rows)          # INSERT OR IGNORE: de-duplicated by event_id
                self._store.mark_imported([r.event_id for r in rows])
        return added, foreign, ok
