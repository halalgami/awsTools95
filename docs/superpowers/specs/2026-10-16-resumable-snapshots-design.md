# AWS 95 Tools — Resumable, sectioned snapshots

**Date:** 2026-10-16
**Status:** Design approved by the owner in brainstorming (2026-10-16). Awaiting written-spec review.
**Builds on:** the first-drop spec (`docs/superpowers/specs/2026-10-07-first-drop-design.md`), §3.3 (local store) and §3.4 (SnapshotService). This spec replaces §3.4's "one job per collector" execution model; everything else in the first-drop spec still holds.

---

## 1. Why

A real run on the owner's machine (2026-10-15) showed three problems:

1. **A timeout throws away finished work.** Each collector ran all of its regions in one job with one time limit. Lambda finished one region, then hit its 180 s limit in the second; both regions were recorded as failed, with nothing saved.
2. **Some collectors are slow by design.** Lambda reads one setting per function and S3 one per bucket, one after another. With hundreds of functions or buckets that takes minutes.
3. **You can't see what is happening.** During a snapshot the app shows "dev: 7/19 collectors". It doesn't say which account is at which stage, what has finished, what was denied, or what is left. After a cancel, a quit or a timeout, the only option is a new snapshot that repeats everything.

### What the owner decided

| # | Decision |
|---|---|
| R1 | A snapshot is split into **sections**, one per (account, collector, region). Each section is saved the moment it ends. |
| R2 | **Resume continues the same snapshot**: it runs only the sections that are not finished, then recomputes findings. Only a snapshot under 24 hours old can be resumed; an older one stays partial and the user takes a new one. |
| R3 | A slow, throttled or failed section is marked and **the run carries on**. Denied sections are marked denied, and Resume skips them unless the user asks it to retry them. The end of the run lists what can be resumed, per account. |
| R4 | Progress lives in a new **Run** tab in the Snapshots window: every section of the current or latest snapshot, grouped by account, with its state. The tray words and the end-of-run balloon stay, and open that tab. |
| R5 | The section plan and every section's state are **stored**, so the picture survives a quit or a crash, and every window reads the same truth. |

### Success means

- A timeout or a cancel never loses a section that finished.
- At any moment the user can see, per account, what is done, what is running and what is left, and why each unfinished section isn't done.
- Resume fetches only what is missing, and never repeats a finished section.
- Nothing about the read-only, cost or honesty rules changes.

---

## 2. Rules carried forward (binding)

Everything in the first-drop spec §2 and §2.1 still holds:

- **Read-only:** every AWS call still goes through `aws95.aws.gate`. No new AWS operations are added.
- **Cost transparency:** Billing sections are opt-in per account, and the cost note is shown before anything runs. Resume runs a Billing section only for an account that opted in, and the Resume button states the cost when any Billing section is still unfinished.
- **Honesty:** a section that did not run is never shown as empty or as done. Partial coverage is always labelled.
- **Manual:** snapshots and Resume start only when the user asks. Nothing runs at boot.
- **Demo mode** never writes the store and shows a demo Run tab from fixtures.
- **Dark and 110–150 % font** are Important; the Run tab gets Light, Dark and Dark-150 % goldens.

---

## 3. Sections

### 3.1 What a section is

A section is one collector in one region of one account:

- `(account, collector, region)`, where `region` is a region code for regional collectors and `"global"` for global ones (account, IAM, S3, budgets, cost, free tier).
- Example: "prod · Lambda · eu-west-1", "prod · S3 · global".
- The plan for a snapshot is every collector × every region the account covers (spec F5), minus the Billing collectors when the account has not opted in.

### 3.2 States

| State | Meaning | Resume runs it? |
|---|---|---|
| `waiting` | planned, not started | yes |
| `running` | in progress now | — |
| `done` | finished, everything read | no |
| `partial` | finished, but some calls inside were denied (its data is saved) | no (the denied count is shown) |
| `denied` | the collector's own listing call was refused, so nothing could be read | only when "retry denied sections" is ticked |
| `timed_out` | exceeded the section's time limit | yes |
| `failed` | any other error (throttling, network, AWS error) | yes |
| `stopped` | cancelled or the app quit while it ran | yes |

A section that is `running` in the store when the app starts (the app crashed or was killed) is read as `stopped`.

### 3.3 Time limits

Each section has its own time limit: the collector's `timeout_s`, applied per region rather than per collector. Lambda's limit becomes 300 s, in line with the other collectors that fan out per item.

---

## 4. Storage

### 4.1 Migration v1 → v2

The store gets its first migration (`PRAGMA user_version` 1 → 2), using the versioned-migration mechanism from first-drop §3.3. It adds one table:

```sql
CREATE TABLE snapshot_sections(
  snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
  collector   TEXT NOT NULL,
  region      TEXT NOT NULL,
  state       TEXT NOT NULL CHECK (state IN ('waiting','running','done','partial','denied',
                                             'timed_out','failed','stopped')),
  started_at  TEXT,
  ended_at    TEXT,
  rows        INTEGER NOT NULL DEFAULT 0,      -- resources found by this section
  denials     INTEGER NOT NULL DEFAULT 0,
  error       TEXT,                            -- classify(...).message only, never str(exc)
  PRIMARY KEY(snapshot_id, collector, region));
```

- **Existing snapshots** get sections derived from `collector_runs` during the migration: one per requested region; `done` when the region is in `regions_covered`, otherwise `failed` with the run's error. They are older than 24 hours in practice, so they can't be resumed; the Run tab can still show them.
- A store written by v2 is refused by an older app (`StoreTooNew`), as today.

### 4.2 Saving as sections end

- A section's resources and metrics are written in the same transaction as its state change to an end state, so a section is never `done` without its data, and never has data while still `waiting`.
- `collector_runs` stays, as the per-collector summary the existing windows read. It is recomputed from the sections whenever a section of that collector ends: `regions_covered` is every region whose section is `done` or `partial`; `status` is `ok` when at least one region is covered; `error` joins the unfinished sections' errors; `denials` is the sum.
- The snapshot's `status` is `complete` only when every planned section is `done` or `partial`. Otherwise it stays `partial`.

### 4.3 Retention

Section rows are kept as long as their snapshot. Clear Local Data… deletes them with everything else.

---

## 5. Running a snapshot

### 5.1 Planning

`take` writes the snapshot row and its section plan (every section `waiting`) before the first AWS call, after the identity check (first-drop Review Focus 1: a profile that now signs in to a different account writes nothing).

### 5.2 Workers

- Accounts run one after another. Within an account, up to 4 sections run at once (`MAX_PARALLEL`), each with its own session and its own time limit.
- A section's state goes `waiting → running → <end state>`, saved at each step.
- **Denied:** when the collector itself raises an error that `classify()` reports as denied (its listing call was refused), the section ends `denied`. When only some calls inside were refused (the collector returns with a denied count), it ends `partial` with that count, as today.
- **Time limit or error:** the section ends `timed_out` or `failed`, with the `classify()` message, and the worker takes the next section. The run never stops for one section.
- **Cancel or quit:** running sections end `stopped` (a worker that doesn't return within the grace period is abandoned, as today), and waiting sections stay `waiting`.

### 5.3 Faster fan-out

- **Lambda:** the per-function `ListProvisionedConcurrencyConfigs` calls run up to 4 at a time instead of one after another.
- **S3:** the per-bucket calls run up to 4 at a time.
- Both still go through the gate with the same reviewed pairs. Results are identical to the sequential version (tested).

### 5.4 Findings

Findings for a snapshot are computed when its run ends, and again after every Resume. A rule whose sources are not all covered is unverified, never resolved (as today).

### 5.5 End of run

- The outcome lists, per account: sections finished, sections that can be resumed, and sections denied. Example: "dev: 41/42 sections done · prod: 2/42 done, 40 denied".
- The end-of-run balloon (job feedback, 2026-10-15) uses that line, and opens the Run tab.
- When at least half of an account's sections are `denied`, the summary also says: "prod: most sections were denied — check its permissions (My Accounts ▸ Permissions)".

---

## 6. Resume

- **Where:** a Resume button in the Run tab; a "Resume the last snapshot" choice in the snapshot prompt when the latest snapshot can be resumed; Start ▸ Programs ▸ Resume Snapshot and Ctrl+K "Resume Snapshot".
- **What it runs:** the latest snapshot of each chosen account, when it is under 24 hours old and has unfinished sections. It runs the `waiting`, `stopped`, `timed_out` and `failed` sections, plus `denied` ones when "Retry denied sections" is ticked.
- **Cost:** a Billing section is resumed only for an account that opted in. When one is in the plan, the Resume control shows `BILLING_COST_NOTE` before it runs.
- **Too old:** a snapshot over 24 hours old shows "This snapshot is too old to resume; take a new one", and Resume is disabled for it.
- **Identity:** Resume checks the account's identity first, exactly as `take` does. A mismatch writes nothing.
- **One job at a time:** Resume and Take Snapshot share the same "one snapshot at a time" rule, and Clear Local Data… refuses while either runs.

---

## 7. What the user sees

### 7.1 The Run tab (Snapshots window)

- **Contents:** the sections of the running snapshot, or of the latest snapshot of each account when nothing runs.
- **Grid columns:** Account, Section (collector), Region, State, Rows, Denied, Time, Error. Grouped by account, with a header row per account that reads its stage, for example "prod — 12/42 sections · running: Lambda eu-west-1" or "dev — complete".
- **States** are shown as words with the existing status colours, never colour alone.
- **Controls:** Resume, "Retry denied sections", and Cancel (while running). Take Snapshot stays on the toolbar.
- **Live updates:** the tab re-reads the store at most twice a second while a snapshot runs (no AWS calls; the store is the source of truth).
- **Filter** box as on the other grids (for example `state:timed_out` or `account:prod`).

### 7.2 Elsewhere

- **Tray words:** "Snapshot prod: 12/42 · Lambda eu-west-1". Clicking them opens the Run tab.
- **History tab:** each snapshot row gains a "Sections" column ("40/42").
- **My Accounts ▸ Collector health:** unchanged columns, now computed from the sections.

---

## 8. Contracts and protocols

- `aws95.contracts.snapshots` gains:
  - `SectionState` (the eight states as a `Literal`);
  - `SnapshotSection(account, collector, region, state, started_at, ended_at, rows, denials, error)`;
  - `AccountStage(account, snapshot_id, done, total, resumable, denied, running: tuple[str, ...], resumable_until: datetime | None)`;
  - `SnapshotProgress` gains `collector` and keeps `region`; its `.text` becomes "prod: 12/42 · Lambda eu-west-1".
- `SnapshotsProvider` gains:
  - `sections(scope, snapshot_id=None, *, cancel=None) -> Result[tuple[SnapshotSection, ...]]` (latest per account when `snapshot_id` is None);
  - `stages(scope, *, cancel=None) -> Result[tuple[AccountStage, ...]]`;
  - `resume_snapshot(scope, request: ResumeRequest | None = None, *, progress=None, stop=None, cancel=None) -> Result[SnapshotOutcome]`, where `ResumeRequest(accounts, retry_denied=False, include_billing_for=frozenset(), labels={})`.
- Demo and empty providers answer from fixtures or NOT_CONNECTED; demo never writes.

---

## 9. Testing

- **Store:** migration v1 → v2 on a v1 database (sections derived from `collector_runs`); `StoreTooNew` for v3; a `running` section read as `stopped` after reopen; resources and section state written in one transaction.
- **Service:**
  - one region timing out keeps the other region's data and marks the collector partial;
  - cancel mid-run: running sections `stopped`, waiting ones `waiting`, finished ones kept;
  - denied listing → `denied`; denied inner calls → `partial` with the count;
  - Resume runs only unfinished sections, never a `done` one, and skips `denied` unless asked;
  - Resume of a snapshot over 24 h is refused;
  - Resume checks identity; a mismatch writes nothing;
  - Billing sections are never planned or resumed without the opt-in (no `ce` call);
  - findings recomputed after Resume.
- **Fan-out:** the parallel Lambda and S3 reads return the same resources as the sequential ones; denials are still counted.
- **Gate:** the exact-calls test is unchanged (no new pairs).
- **Providers:** `sections` and `stages` from a seeded v2 store; "not resumable" text for an old snapshot.
- **Windows:** the Run tab groups by account, shows each account's stage, enables Resume only when something is resumable, states the Billing cost when needed; the tray words; the end-of-run summary and the "most sections were denied" line.
- **Goldens:** the Run tab (running and finished-with-gaps) in Light, Dark and Dark-150 %.
- **Real-device check (owner):** cancel a two-account snapshot halfway, quit, restart, open the Run tab (states survive), Resume, and confirm only the unfinished sections run.

---

## 10. Out of scope

- Sections finer than collector × region (for example per Lambda function or per bucket). The fan-out speed-up in §5.3 makes them unnecessary for now.
- Scheduled or automatic resume (first-drop F1 stays manual).
- Running two accounts in parallel.
- Changes to what each collector reads.
