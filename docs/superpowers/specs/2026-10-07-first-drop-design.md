# AWS 95 Tools — First Drop: Snapshots (2b), Activity (2c), Publishing (2p)

**Date:** 2026-10-07
**Status:** Approved in brainstorming (2026-10-06/07). Awaiting written-spec review.
**Builds on:** phase 2a (`docs/superpowers/specs/2026-10-05-phase2a-foundation-accounts-design.md`). That spec's §10 was the 2c design input; this spec supersedes it where they differ.
**Branches:** one implementation branch per part, created from `feature/ui-revamp-spec`: `feature/2b-snapshots`, then `feature/2c-activity`, then `feature/2p-publish`.

---

## 1. Intent

The first public release of AWS 95 Tools goes to **github.com/halalgami/aws95**. In it, every main window shows real data from the user's AWS accounts.

| Part | Scope | Plan |
|---|---|---|
| **2b** | The labsnap engine: snapshots feed Resources, Findings, Fleet, Snapshots, Billing, and collector health in My Accounts | own plan |
| **2c** | The ctwatch engine: the Activity Log (choose how much history, background lookup load, Import History… from S3) | own plan |
| **2p** | Publishing: scrub, README, LICENSE, SECURITY, app bundle name, pre-push check, one clean public commit | own plan |

2d (AI Insights, Reports, Search) is not in this release. Those windows keep their honest "arrives in a later update" state.

### What the owner decided

| # | Decision |
|---|---|
| F1 | Snapshots are **manual**. When the newest snapshot of a roster account is more than a day old, the app **prompts**: "It's been N days since your last snapshot. Take one now?" It never runs one automatically. Keep it simple. |
| F2 | Billing comes **from the snapshot**: 4 Cost Explorer requests (≈ $0.04) per account per snapshot — monthly costs by service, daily costs this month, the forecast from tomorrow to month end, and the active cost-allocation tags (one more request per extra page AWS returns; no forecast request on a month's last day). Opt-in per account; every query is filtered to that account, so a payer never double-counts its members. No live Cost Explorer calls from the Billing window. (Revised 2026-10-07, ruling D1.) |
| F3 | Findings: all 38 labsnap rules as shipped, plus **Accept / Un-accept** (a mute with a reason and optional expiry) in the UI. The Rules tab is read-only. There is no rule config file. |
| F3-future | User-supplied rule folders (labsnap's `extra_dirs`) are wanted later, as a separate deep dive with a security review, because the app would run code from outside its own package. Recorded in §9. |
| F4 | Activity's first load uses the CloudTrail lookup API in the background, newest first, and can resume. The user chooses how much history: **7 days is recommended**; 30 and 90 days are offered with real time estimates and a plain warning that they take a while. |
| F5 | Regions: snapshots and Activity cover **each account's default regions**. If an account has none set, they cover the profile's region plus `us-east-1`. The prompt shows the regions it will cover and offers **Change regions…**. |
| F6 | The repo is **public**. Publishing includes a scrub and a fresh single-commit history. |
| F7 | The license is **MIT**. |
| (2a §10) | Athena is dropped. CloudTrail history beyond the lookup API comes from the trail's S3 files into SQLite. Event scope is ctwatch's scope plus five additions. |

### Success means
- A new user downloads the app, runs the wizard, accepts the snapshot prompt, and within minutes sees their resources, findings, fleet matrix and (if they opted in) costs.
- They pick 7 days of activity and see who did what.
- Every view says how old its data is and what it covered. Nothing reads "empty" when the truth is "not looked at".
- The public repo contains no organisation-internal identifiers. The pre-push check proves it.

---

## 2. Rules carried forward (binding)

These are the phase-2a rules from CLAUDE.md and the 2a spec §2. In short:
- **Read-only.** Every AWS API call goes through `gate.call`/`paginate`, using reviewed `(service, operation)` pairs.
- **Secrets.** No secret values in any contract, store, log or UI.
- **Money.** Money is `Decimal` end to end and is **stored as exact decimal text**, never as a float.
- **Honesty:**
  - A failure is never shown as empty.
  - Partial coverage is always labelled.
  - Demo data is always watermarked.
  - Demo mode never writes settings or the store.
- **Plain text.** Provider text is shown as plain text and sanitised.
- **Opening a window never calls AWS.** Only Take Snapshot, Update activity, Import History…, Verify and Sign In do.
- **Dark mode and large fonts.** Dark and 110–150 % font are Important. New screens get Light, Dark and Dark-150 % goldens.
- **No network in tests.** Everything is hermetic.


### 2.1 Cost transparency (owner, 2026-10-07; binding)

Some AWS calls cost money. The user must never be surprised by a charge.
- **Every control that can cause a billed call states the cost at the point of choice, before anything runs.** This covers Cost Explorer, about $0.01 per request, and S3 requests and data transfer for Import History…:
  - the snapshot prompt's "Include Billing for" checkboxes: "≈ $0.04 per account per snapshot (AWS Cost Explorer charges)";
  - My Accounts' "Include Billing in snapshots" checkbox: the same text, next to the label, not only in a tooltip;
  - the Billing window's "Billing isn't enabled… Enable…" path: the same text in the confirmation;
  - the setup wizard's "Also check Billing access (AWS charges $0.01 per check)", unchanged;
  - Import History…: "Reads your CloudTrail log files from S3. AWS bills S3 requests and data transfer to your account (usually cents for a few days of logs; larger ranges cost more)". The dialog also shows the number of days and the estimated file count once the bucket is found, before Start.
- **Free paths are never labelled as paid,** and **paid paths are never silent.** Opening a window, the CloudTrail lookup API, the STS/identity checks and all labsnap collectors except Cost Explorer have no per-request charge.
- **One source for the wording.** All cost text comes from constants in `aws95/contracts/billing.py` (contracts are importable by programs, the shell and the AWS layer alike):
  - `BILLING_COST_NOTE`, for Cost Explorer;
  - `IMPORT_COST_NOTE`, for S3 imports;
  - `BILLING_CHECK_NOTE`, for the wizard probe.

  The UI and the README render these constants. A test asserts that every such control's visible text contains the matching constant.
- **README:** a "What costs money" section, generated from the same constants. It lists each paid action, what AWS bills, and the approximate amount. It says plainly that everything else in the app is free to run.

---

## 3. Shared foundation (built in 2b, reused by 2c)

### 3.1 Engine code
- labsnap's library layers (`collect/`, `rules/`, `analyze/`, `store/`, `model.py`) are copied into `aws95/aws/engines/labsnap/` and adapted:
  - every boto call goes through the gate;
  - they take a session from `session_for`;
  - money becomes Decimal;
  - their output is mapped to contracts by `aws95/providers/live/`.
- labsnap's `cli.py`, `render/` and TOML config loader are **not** copied.
- ctwatch's `ingest/`, `rules.py` and `store/` go to `aws95/aws/engines/ctwatch/` in the same way. Its `tui/`, `cli.py` and `athena.py` are not copied.
- **Provenance and drift.** Each copied file starts with one comment line naming its upstream file and commit. aws95 owns the code from then on.

### 3.2 Gate growth
- `gate.ALLOWED` gains exactly the operations the copied collectors and ctwatch call.
- Each entry needs a review comment saying what the call returns. A test asserts that every operation the engines call is listed, and nothing more. The plan lists the pairs, derived from labsnap's policy (≈ 58 IAM actions).
- **Three responses are redacted** inside the collector, before anything is stored or turned into a contract. Each redaction has a test:
  - `lambda.list_functions` → `Environment.Variables`;
  - `cognito-idp.describe_user_pool_client` → `ClientSecret`;
  - `ecs.describe_task_definition` → container `environment` and `secrets` values.
- **Cost Explorer gains** `get_cost_forecast` (billed). `get_cost_and_usage` is called twice per snapshot: monthly by service, and daily for the current month.
- **S3 (2c only):** `s3.list_objects_v2` and `s3.get_object` sit behind a **parameter validator** (§5.4).

### 3.3 Local store
- **One SQLite database** at `app_dir()/aws95.db`.
  - The directory is already 0700. The file is created 0600, and its permissions are re-checked at open.
  - Versioned migrations, with `PRAGMA user_version`.
  - WAL mode. One writer thread at a time, serialised by a lock. Readers get their own connections.
- **Tables:**
  - labsnap's `snapshots`, `collector_runs`, `resource_state`, `metrics`, `findings`, `mutes`, `seeded_findings`, all keyed by account ID;
  - ctwatch's `events`, plus `activity_coverage` (account, region, from, to, kind);
  - `meta`.
- **Money and metric values are TEXT** holding the exact decimal string AWS returned. They are converted to `Decimal` when read. labsnap's float rounding (`round(sum(float…), 2)`) is removed; totals come from AWS's own `Total` or from Decimal sums of the service partition.
- **Activity raw JSON** is stored zlib-compressed.
- **Retention setting:** "Keep activity for N days", default 90. Imported ranges are kept until the user clears them.
- **Store location and backup:** `aws95.db` holds account metadata, findings and activity, so it is sensitive. It is never exported automatically, and **Settings ▸ Clear local data…** deletes it, after a confirmation.

### 3.4 SnapshotService (Qt-free, runs on the TaskRunner)
- **`take(accounts, include_billing_for, regions_for, cancel, progress)`** runs collectors per account × region:
  - with bounded parallelism: 4 collectors at once per account, with accounts in sequence;
  - with per-collector timeouts;
  - and it can be cancelled.
- **Progress** is reported through a thread-safe callback (to the UI's relay): "dev: 7/19 collectors · me-south-1".
- **Results are saved as a run completes.**
  - A cancelled or failed run is saved as **partial**, with exact `regions_requested` and `regions_covered` per collector.
  - Denied calls are counted per collector.
- After a snapshot, **findings** are computed with labsnap's `analyze/findings.py` (first-seen, seeded, mutes).
- Only one snapshot runs at a time. A second request is refused with "A snapshot is already running", and the running one is shown.

### 3.5 The refresh prompt (F1)
- **When:** at boot, after the desktop appears (and after the setup wizard, if it is showing), and when a data window opens.
  - Condition: live mode, a non-empty roster, and the newest snapshot of any roster account older than **1 day**, or missing.
  - Shown at most once per app session, unless the user asks for it.
- **Dialog** (non-modal, Win95 style). Example text: "It's been 3 days since your last snapshot (dev, prod)."
  - **Covers:** the per-account region list, then "+ global services".
  - **Include Billing for:** a checkbox per account, defaulting to the saved per-account choice. The note says "≈ $0.04 per account (AWS Cost Explorer charges)" (ruling D1).
  - **When Activity has history:** a checkbox "Also update Activity" (2c).
  - **Buttons:** **Take Snapshot**, **Change regions…** (opens the account's region list, then saves it per account) and **Not now**.
- **On demand:** the Snapshots toolbar, the Start menu (Programs ▸ Take Snapshot) and Ctrl+K "Take Snapshot" all open the same dialog.

---

## 4. 2b: what each window shows

All reads come from the store, so no window calls AWS when it opens. Every Result carries `Freshness(taken_at, "snapshot")`, and data more than 1 day old shows the existing STALE banner. The scope bar's account × region filter applies. The status bar shows the age and coverage, e.g. "Snapshot 2 h ago · 3 regions". **Demo mode** keeps the existing demo providers and fixtures unchanged.

| Window | Content |
|---|---|
| **Resources** | **Type tree:** the types in the latest snapshot per account, with counts. **Rows:** paged per type (name, state, region, key attrs, tags); partial rows marked. A region the snapshot didn't cover shows "Not collected in <region>", never an empty grid. |
| **Findings** | Open, resolved and accepted findings across snapshots (first-seen, age, severity, detail). **Accept…** asks for a reason and an optional expiry and stores a mute locally; **Un-accept** removes it. Nothing is written to AWS. **Rules tab:** all 38 rules, read-only (id, title, description, severity, enabled). |
| **Fleet** | A rule × account matrix from each account's latest snapshot: ✓ none / ✗ or N open / ? not collected. Clicking a cell navigates to Findings filtered to that rule and account. |
| **Snapshots** | **List:** time, status, regions, collector health. **Diff** of two snapshots uses labsnap's comparability rule: a resource absent where one side didn't look is reported as **excluded (not looked at)**, never as removed. **Trend:** stored metrics over time. **Take Snapshot** is in the toolbar. |
| **Billing** | **Summary:** month-to-date, last month, month-end forecast. **Breakdown:** by service. **Trend:** monthly over 13 months, or daily for this month. **Free tier:** usage. All values are exact Decimal, shown with `format_value(…, "money")`. An account without Billing enabled shows "Billing isn't enabled for this account" with **Enable…**. Breakdowns by account across accounts, by region and by usage type, and uncovered spend, show "Not in this release", never empty. |
| **My Accounts** | **Collector health** from the latest snapshot: status, duration, regions covered, denied calls. **New checkbox:** "Include Billing in snapshots", saved per account. |

---

## 5. 2c: Activity

### 5.1 First use
- When the Activity window opens for an account with no history, it shows **"Load activity history"**.
  - **Choices:** 7 days (recommended, the default), 30 days, 90 days.
  - **Estimates:** each choice shows an estimate per region, computed from a sample. One `lookup_events` call over the last hour counts events. The estimate assumes 50 events per call at about 2 calls per second, and is displayed as "≈ N min".
  - **Wording:** "Large ranges take a while. Loading runs in the background — keep working; the newest events appear first."
- The user can cancel the load, and it resumes after cancel, quit or restart.

### 5.2 Loading
- **Source:** `lookup_events`, in 4-hour windows, **newest first**, per region in the account's region list. `us-east-1` is always included, for global services.
- **Passes:** a bulk pass of change events (`ReadOnly=false`), then targeted passes for the extra event names.
- **Saved progress:** stored per (account, region, kind) in `activity_coverage`.
- **Progress display:** in the status bar and tray, e.g. "Activity dev: 23/90 days · me-south-1".
- **Update activity:** F5 in Activity, or the prompt's checkbox, fetches only events newer than the stored coverage.

### 5.3 Event scope
One editable rules table, in the engine.
- **Base (ctwatch):** every change event; human `AssumeRole*`; human `GetSecretValue`.
- **Additions:**
  1. **All root activity:** a targeted pass with `LookupAttributes=[Username=root]`, which covers root's read events too. Root's change events already arrive in the bulk pass. The plan verifies against real data that root events carry `Username=root`.
  2. **Denied attempts:**
     - **Change attempts:** denied *change* attempts are always covered, because the bulk pass (`ReadOnly=false`) includes events with an `errorCode`.
     - **Read attempts:** denied *read* attempts aren't available through the lookup API without pulling every read event, about 20k calls for 90 days. They come **only from Import History… (S3)**.
     - **What the window says:** "Denied change attempts: covered. Denied read attempts: from imported history only." (This supersedes the 2a-spec idea of tracking them forward-only.)
  3. **Sensitive human reads:** `GetParameter`/`GetParameters`, `kms:Decrypt`, `GetSessionToken`, `GetFederationToken`, `ListAccessKeys`, `GetCredentialReport`, each as a targeted pass, humans only.
  4. **`ConsoleLogin`, including failures:** a targeted pass. Verify against real data whether these arrive as change events.
  5. **CloudTrail Insights events:** from the S3 import only.
- **Risk rules:** ctwatch's `rules.py`, unchanged (escalation, destructive, exposure, security-config, secret access). It is generic.

### 5.4 Import History…
- **Entry point:** the Activity menu. It takes a date range and runs as a cancellable background job, reporting progress in days.
- **Finding the trail:** `cloudtrail.describe_trails` gives the bucket, prefix, KMS key and organisation flag. If the bucket is in another account (an org trail), the dialog says so and lets the user pick that log-archive account's profile from the roster.
- **Permissions check:** probe `s3:ListBucket` and `s3:GetObject` on that exact prefix, and `kms:Decrypt` if the trail is encrypted. A missing permission produces a plain message plus the exact policy lines to add.
- **Gate validator:**
  - `list_objects_v2` and `get_object` are allowed only when `Bucket` equals the trail bucket the dialog resolved.
  - `Prefix` or `Key` must sit under `<prefix>/AWSLogs/[<org-id>/]<account>/CloudTrail/` or `CloudTrail-Insight/`, and keys must end `.json.gz`.
  - Anything else raises `WriteBlockedError`.
  - The validator is installed only for the duration of an import, scoped to that bucket.
  - Tests cover allowed keys, other buckets, other prefixes, path traversal (`..`) and other extensions.
- **Processing:** decompress, parse, apply the same normalisation and rules, insert with de-duplication by `event_id`, and record coverage per day.

### 5.5 Window and contracts
- **Window:** the existing Activity views (timeline, principals, top-N, filter, raw event) query the store only. The raw event shows as plain text.
- **Contract change:** `AthenaStatus` is replaced by **`HistoryStatus`**:
  - per-region coverage ranges;
  - the denied-tracked-since date;
  - whether a load or import is running;
  - progress.
- **Protocol change:** `ActivityProvider.athena_status` becomes `history_status`, and the demo and empty providers are updated to match.

---

## 6. 2p: Publishing

### 6.1 Scrub
| Find | Replace with |
|---|---|
| Real account IDs (any 12-digit ID that is not a documented placeholder) | `123456789012` |
| The former lab CloudTrail bucket name | `example-cloudtrail-bucket` |
| The organisation's name, its abbreviation and its department name | removed |
| Wording that ties the rules to the organisation's sector | "built for regulated environments" |
| Private paths and names of the original engine repositories | "the original labsnap/ctwatch tools" |

The identifiers themselves never appear in the public tree: `tools/publish_check.py` holds them only as SHA-256 digests.

### 6.2 Published tree
- **Included:** code, tests, goldens, assets, `docs/packaging.md`, `docs/PROJECT_CONTEXT.md`, the specs, and `CLAUDE.md`. Everything is scrubbed.
- **Excluded:**
  - `docs/superpowers/plans/` and `docs/superpowers/followups/` (private internal review history);
  - the phase-1 legacy files that phase 2 replaces: `core/`, `database.py`, `credentials_handler.py`, `config.py`, `lambda_report.py`, `s3_report.py`. These are deleted from the codebase in 2b once nothing references them; `tests/test_layers.py` already guards that.
- **New files:**
  - **`LICENSE`:** MIT, "Copyright (c) 2026 Algam The Dev".
  - **`README.md`:**
    - What it is, with screenshots taken from the goldens.
    - The read-only promise.
    - Install from source (`uv sync`, `uv run python -m aws95`) and the packaged build.
    - The first-run wizard.
    - The IAM policy, generated from `permissions.py`, plus the Billing cost note.
    - Privacy: everything stays local; AWS is the only network destination.
    - Demo mode (`--demo`), and how to contribute.
  - **`SECURITY.md`:** report vulnerabilities through GitHub private advisories.
- **App bundle:** a small entry module makes the packaged app **"AWS 95 Tools.app"** (Windows: `AWS 95 Tools.exe`). Signing and notarisation stay on the release track.
- **Version:** `3.0.0`.

### 6.3 Pre-push check
`tools/publish_check.py` runs as a test (`tests/test_publish_check.py`) on every commit. It **fails** on any of:
- a 12-digit account ID other than `123456789012` (documented false positives, such as timestamps, are allowed explicitly);
- an ARN containing a real account ID;
- an email address, except `noreply@anthropic.com` in the commit trailers and placeholder `example.com` addresses;
- AWS access-key IDs or secret-key patterns;
- the old bucket names;
- the organisation's name or abbreviation as a word (matched by digest);
- gitignored data files present in the tree.

### 6.4 History and push
1. Create the orphan branch `public` from the checked tree as **one commit**: "Initial public release: AWS 95 Tools 3.0.0".
2. Set `origin` to `github.com/halalgami/aws95` (the owner creates the empty repo).
3. Push only `public`, as `main`. Private branches never leave the machine.
4. **The push requires the owner's explicit OK at that moment.**

Later releases are cut the same way: scrub check, then a commit onto `public` from the integration branch's tree.

---

## 7. Testing

- **Store:**
  - migrations;
  - 0600 permissions;
  - Decimal round-trip, with no float anywhere in the money path (an AST test bans `float(` in the cost collector and the store);
  - concurrent read while writing;
  - retention pruning;
  - Clear local data.
- **Collectors:**
  - Stubber fixtures per collector, carried over from labsnap's pseudonymised fixtures and checked by the scan;
  - a redaction test for each of the three secret-bearing responses;
  - a denied call becomes partial coverage;
  - a failed region marks it not covered.
- **SnapshotService:** progress, cancel → partial save, one at a time, billing opt-in honoured (no `ce` call when off).
- **Gate:**
  - `ALLOWED` equals exactly the set of calls the engines make;
  - the S3 validator's allowed and blocked cases;
  - the AST ban still holds.
- **Providers:**
  - each window's provider against a store seeded from fixtures;
  - "not collected" vs empty;
  - stale freshness.
- **Prompt:**
  - shown for a stale or missing snapshot;
  - once per session;
  - Not now;
  - Change regions…;
  - billing checkboxes saved per account;
  - never in demo or offline mode.
- **Activity:**
  - estimate maths;
  - newest-first windows;
  - resume after cancel;
  - incremental update;
  - targeted passes;
  - denied-since tracking;
  - Import History… with fake S3 objects (gzip JSON), including org-trail profile selection and KMS-denied messaging;
  - de-duplication.
- **UI goldens:** Light, Dark and Dark-150 % for the prompt, the first-use dialog, Import History…, and each window in its live-populated state (seeded store).
- **Publish:** the scan runs on the tree, and a synthetic leak test proves each pattern is caught.
- **Real-device check (owner):**
  - first snapshot on the lab account;
  - Billing opt-in once;
  - 7-day Activity load;
  - Import History… for 1 day;
  - Dark-150 % across the windows;
  - the packaged app named correctly.

---

## 8. Execution

- **Order:** three plans, run back to back, subagent-driven, as in 2a (one review per task, a final review per part, one fix wave). Each part merges into `feature/ui-revamp-spec` before the next starts.
- **Merges:** need the owner's OK, as always.
- **The push:** 2p ends at the push, which needs a separate explicit OK.

---

## 9. Future work (recorded, not built)

- **F3-future: custom rule folders.** Loading user rule files (labsnap `extra_dirs`). Needs its own deep dive: sandboxing or signing, review UX, and an IS security review, because it runs code from outside the package.
- Live Billing breakdowns by account across accounts, by region and by usage type, plus uncovered spend: extra Cost Explorer calls inside the snapshot.
- Rule tuning in the UI: enable or disable rules and change severity (option B in brainstorming).
- Scheduled snapshots (option C in brainstorming).
- 2d: AI Insights (Bedrock), Reports, Search.
- Release track: signing and notarisation, a Windows packaged build test, auto-update.
- 2a follow-ups carried forward (see `2026-10-06-phase2a-followups.md`): ScopeBar reload generation guard, wizard sign-in via `shell.sign_in`, and CSV export PARTIAL provenance. This one becomes relevant with live paged Resources in 2b, so **2b fixes it**: an export of a PARTIAL result includes a header row or a status notice.
