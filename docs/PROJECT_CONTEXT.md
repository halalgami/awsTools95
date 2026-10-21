# Project context (portable)

This file carries the decisions, status and working style that would otherwise live only in one machine's Claude Code memory (`~/.claude/projects/<path>/memory/`). Read it with `CLAUDE.md` when you resume on a new machine. Last updated 2026-10-16.

## Status

- **First drop (2b snapshots, 2c activity, 2p publishing) is complete** and merged into `feature/ui-revamp-spec`. Version 3.0.0. The public repo is `https://github.com/halalgami/awsTools95`: the scrubbed `public` branch is pushed there as `main` once the owner gives the explicit OK (spec `docs/superpowers/specs/2026-10-07-first-drop-design.md` §6). `tools/publish_check.py` runs in the suite and must stay green; later releases are one commit onto `public` (CLAUDE.md ▸ Git workflow; the full recipe is in the private 2p plan).
  - The test suite has 2202 passing tests and 4 skipped.
- **After the first drop (2026-10-15/16):** visible job feedback (tray words and a balloon when a snapshot or Activity job ends), a ScopeBar reload guard, the wizard's Sign In shared with the shell, Dark radio rings, a one-line Fleet cost note, stable live goldens, and fixes to the open-SG and external-KMS rules.
- **Resumable snapshots (2026-10-16, merged at `793ee87`):** a snapshot runs as stored sections (account × collector × region), each with its own time limit and end state (done, partial, denied, timed out, failed, stopped). Store schema v2 adds `snapshot_sections` (old snapshots are migrated). A Run tab in Snapshots shows each account's stage; Resume (Run tab, prompt, Start menu, Ctrl+K) finishes only the unfinished sections of the same snapshot when it is under 24 hours old. Lambda and S3 per-item reads run 4 at a time; Lambda's limit is 300 s per region. Suite: 2266 passed, 4 skipped, ≈60 s. Spec `docs/superpowers/specs/2026-10-16-resumable-snapshots-design.md`; plan `docs/superpowers/plans/2026-10-16-resumable-snapshots.md` (private); follow-ups `docs/superpowers/followups/2026-10-16-resumable-snapshots-followups.md` (private).
- **Take Snapshot desktop icon** (2026-10-17, merged at `a3f57f0`): the first icon on the desktop.
- **2d part 1: Reports and Search (2026-10-18, merged at `114e074`):** live Reports (inventory, cost, open findings, activity watchlist) with a Coverage block, one `ComposedReports` for demo and live; Ctrl+K also finds resources, findings, principals and billing services from the store and opens the window filtered to the hit. No AWS calls. Suite: 2298 passed, 4 skipped, ≈61 s. Spec `docs/superpowers/specs/2026-10-18-reports-search-design.md`; follow-ups `docs/superpowers/followups/2026-10-18-reports-search-followups.md` (private).
- **Billing complete (2026-10-20, merged at `311151d`):** By account, By region, By usage type and Uncovered spend tabs and Fleet's cost row show real data. The Cost collector makes 6 billed requests (≈ $0.06 per account; README regenerated). A shared service map (`aws95/contracts/service_map.py`) says which collector covers which Cost Explorer service. Suite: 2328 passed, 4 skipped, ≈65 s. Spec `docs/superpowers/specs/2026-10-18-billing-complete-design.md`.
- **Activity and UI polish (2026-10-20, merged at `f91500a`):** the Resources inspector's Related tab lists stored Activity events whose ARNs name the resource (newest 200, that account), with Open in Activity Log (Activity gained `arn`/`account` navigation); Ctrl+K details in a right-aligned muted column; Snapshots History and health columns never elide at 150 %; a Run-tab golden while running. Suite: 2348 passed, 4 skipped, ≈70 s. Spec `docs/superpowers/specs/2026-10-20-activity-ui-polish-design.md`.
- **Findings tools (2026-10-20, merged at `f975735`):** Rules… saves local tuning (enable, severity, parameters checked against shipped types, reset) and re-checks the latest snapshots; disabled rules are hidden everywhere, never shown as resolved; new snapshots use the tuning; Seed finding records an earlier first-seen date; Backfill re-runs today's rules on stored snapshots since a date with progress and Cancel; a shared `JobLock` keeps snapshots and backfills from overlapping. Store schema v3 (`rule_overrides`). Suite: 2382 passed, 4 skipped, ≈72 s. Spec `docs/superpowers/specs/2026-10-20-findings-tools-design.md`.
- **AI Insights (2d part 2, 2026-10-21, branch `feature/ai-insights`, awaiting the owner's merge OK):** per account, a scrubbed aggregate preview (no ids, names, ARNs or IPs; "Account N" labels); a model picked per account from `bedrock:ListFoundationModels`; on Analyze only, one `bedrock-runtime:Converse` call per account in that account and region with exactly the reviewed entry (VALIDATED request: one text message, 1,500 tokens out); plain-text answers with token usage; Export with the payload. The one named exception to read-only. IAM: `bedrock:InvokeModel` in its own statement scoped to Anthropic models (security review). Suite: 2418 passed, 4 skipped, ≈77 s. Spec `docs/superpowers/specs/2026-10-20-ai-insights-design.md`.
- **Next:** the GitHub publish (owner creates `halalgami/awsTools95`; rebuild `public`; push only with OK) and the release track.
- **Phase 1 (UI revamp) is complete.** Milestones M1 (foundation and kit), M2 (shell), M3 (programs) and M4 (perf, accessibility, packaging, legacy removal) are all merged into the integration branch `feature/ui-revamp-spec`.
  - The test suite has 1294 passing tests and 4 skipped; it runs headless in about 40 s (one run took 52 s).
- **Phase 2a (AWS foundation, setup wizard, live Accounts) is complete and merged** into `feature/ui-revamp-spec` (merge `8dc34a5`). The owner's first-run fixes are merged too (`c6432ae`: verification is remembered across sessions, and the colour picker no longer hides the wizard), as are the About credits (`546f1e7`).
  - Spec: `docs/superpowers/specs/2026-10-05-phase2a-foundation-accounts-design.md`. Follow-ups: `docs/superpowers/followups/2026-10-06-phase2a-followups.md` (private; not in the public repo).
- **First drop, part 2b (Snapshots) is complete** and merged into `feature/ui-revamp-spec` (`9f0181c`, executed inline on 2026-10-15): the local store `aws95.db` (schema v1 already holds 2c's `events` and `activity_coverage`), the reviewed gate pairs with `gate.bind`, the 19 labsnap collectors with three secret redactions, 38 rules, findings that never mistake "not looked at" for "resolved", `SnapshotService`, live Resources / Findings (Accept / Un-accept) / Fleet / Snapshots / Billing / collector health, the refresh prompt with Change regions… and per-account Billing, and Clear Local Data…. The phase-1 legacy helpers are deleted. Suite: 1871 passed, 5 skipped, ≈52 s. Plan: `docs/superpowers/plans/2026-10-07-first-drop-2b-snapshots.md` (private; not in the public repo); follow-ups and every ruling: `docs/superpowers/followups/2026-10-07-2b-followups.md`.
- **First drop, part 2c (Activity) is complete** and merged into `feature/ui-revamp-spec` (`f70f61c`, executed inline on 2026-10-15): the ctwatch engine (rules unchanged, secrets redacted before rules and storage, one event-scope table with the five additions, SSO and SAML principals counted as people), newest-first resumable LookupEvents loading with one-call estimates and incremental F5, Import History… from the trail's S3 files (scoped validators, organisation folder pinned via DescribeOrganization, organisation-trail profile pick, KMS check with exact policy lines, per-day coverage, de-duplication), `HistoryStatus` replacing `AthenaStatus`, the history strip and Keep Activity For…. Suite: 2071 passed, 4 skipped, ≈55.36 s. Plan: `docs/superpowers/plans/2026-10-07-first-drop-2c-activity.md` (private; not in the public repo); follow-ups: `docs/superpowers/followups/2026-10-07-2c-followups.md`.
- `main` holds only the pre-revamp baseline. Merging into `main` is the owner's decision.
- **The "first drop", the first public release to github.com/halalgami/awsTools95,** was in three parts, each with its own plan, run in this order:
  - 2b: the labsnap snapshot engine;
  - 2c: the ctwatch Activity engine;
  - 2p: the publishing pass (scrub, README, MIT licence, one clean commit, push).
  - Spec (owner-approved): `docs/superpowers/specs/2026-10-07-first-drop-design.md`. Plans: `docs/superpowers/plans/2026-10-07-first-drop-{2b-snapshots,2c-activity,2p-publish}.md` (private; not in the public repo). All of it is merged into `feature/ui-revamp-spec`.
  - **The plans are locked.** A hard review (4 Critical, 10 Important, 15 Minor) was applied and re-reviewed, and D1–D4 were ruled. Record: `docs/superpowers/followups/2026-10-07-first-drop-plan-review.md` (private; not in the public repo). The owner approved them on 2026-10-07 and they are merged into `feature/ui-revamp-spec`. Spec §2.1 adds cost transparency: every paid AWS action states its cost before it runs.
  - **Start here when resuming:** `docs/superpowers/followups/MASTER-TODO.md` (private; not in the public repo).

Where things are:

| What | Where |
|---|---|
| Master to-do (start here) | `docs/superpowers/followups/MASTER-TODO.md` (private; not in the public repo) |
| Binding design specs | `docs/superpowers/specs/` (phase 1 `2026-10-02-…`, 2a `2026-10-05-…`, first drop `2026-10-07-…`) |
| Milestone plans | `docs/superpowers/plans/` (private; not in the public repo) |
| Rulings, measured budgets, deferred items, real-device checklists | `docs/superpowers/followups/` (private; not in the public repo) |
| Packaging (Nuitka, macOS and Windows) | `docs/packaging.md`, `tools/build_nuitka.py` |

## Design decisions (from brainstorming, 2026-10-02)

- **Platform:** a native desktop app only, no web tech. It targets macOS and Windows and is built with PySide6 Qt Widgets. Performance comes from model/view, a cache-first design, concurrent fetching, pyqtgraph and Nuitka builds. The old UI was thrown away entirely.
- **Look:** "Classic+", meaning authentic grey `#c0c0c0` Win95 chrome, a navy-to-blue title gradient and real bevels, combined with modern fonts, spacing and zebra rows.
  - A Dark theme is required for the owner's eyes. It is a graphite twin with the same bevel geometry, follows the OS with a manual override, and has a font scale of 100–150 %.
- **Layout:** the full Win95 desktop metaphor: a teal desktop, icons, a Start menu, a taskbar and floating program windows. The owner chose it over an Explorer or hybrid layout ("go all in and take me back to my childhood").
- **Whimsy is a core feature**, not polish:
  - original Win95-style sounds and a launch jingle (never Microsoft's copyrighted audio);
  - screensavers that are dismissed by an explicit click, not by moving the mouse;
  - the Cloudy mascot.

  All of it must be toggleable, and none of it may cost speed.

## Phase 2a decisions (spec section 1)

| # | Decision | Choice |
|---|---|---|
| D1 | Account access | Named profiles only: access keys, SSO, and `role_arn` + `source_profile` profiles, which boto3 resolves itself. |
| D2 | Permission check | Live read-only probes, one per capability. No policy simulator. |
| D3 | Profiles found | Wizard checklist. Existing roster entries are pre-ticked; new profiles that verify are ticked. One profile per account. A rescan offers new profiles and needs confirmation. |
| D4 | Expired SSO | Sign In... runs `aws sso login` when the AWS CLI is found. Sign In shows the code in the app (wizard and Accounts) through one non-modal dialog with Cancel and a Copy Command button; when the CLI is missing it shows the install link and the command. Nothing is copied unless the user presses Copy. |
| D5 | Engine code | Copied into `aws95/aws/` and adapted. Only that package imports boto3/botocore. |
| D6 | Partial paging | Keep the rows already fetched, and mark the result PARTIAL with a banner. Exports carry the PARTIAL provenance (generic grid export deferred to 2b, see the follow-ups). |
| D7 | Athena (2c) | Dropped. History is read straight from the trail's S3 log files into SQLite. |

## Roadmap: phase 2

- **Goal:** AWS 95 Tools becomes the owner's one-stop shop for examining and exploring **multiple AWS accounts**.
- **Engines brought in:** from the original labsnap/ctwatch tools.
  - `ctwatch/`: CloudTrail who-did-what, with risk rules, a SQLite cache and an Athena fallback. It is single-account and has the region and bucket hard-coded.
  - `labsnap/`: multi-target state snapshots, with 19 collectors, ok/denied/unreachable probes, a partial-coverage model, 38 rules, diff, trend and fleet views, and versioned SQLite storage.
- **Integration approach:** both engines are Qt-free, read-only Python. Lift their library layers behind the `aws95/contracts` and `aws95/providers` seam, and drop their TUI and CLI layers.
- Phase 1 already gives every ctwatch and labsnap feature a home, through windows, Start-menu entries and views. Phase 2 fills those homes.
- Authentication beyond named profiles (D1) stays out of scope.
- **The phase-1 legacy files** were deleted in 2b; `tests/test_layers.py` guards against their return.

## How the owner likes work run

- **Execution:** milestone plans run **subagent-driven** without check-ins. When the owner asks for "status?", answer with a short table: done, in progress, next, tests, and the number of rulings.
- **Rulings:** decisions made on the owner's behalf are fine if each one is listed at the end with its cost if wrong (`Ruling: <what> — <why> — <cost if wrong>`).
- **Conservative agent usage:** keep the number and complexity of subagents low.
  - Batch small tasks into one dispatch.
  - Prefer mid-tier or cheap models.
  - Keep one review per task, one final review and one fix wave.
- **Mid-run requests:** a feature request that arrives mid-run gets folded into the next fix wave with a ruling, rather than stopping the run.
- **Verification:** the owner verifies by running the app. After a merge they usually ask you to launch it, which you do with `uv run python -m aws95 --demo` in the background.
- **Accessibility severity:** Dark-mode legibility and large font scales (110–150 %) are rated **Important**, never polish.
- **Merges and pushes:** always confirm before merging or pushing.

## Moving to another machine

Tracked in git: all code, tests, goldens, generated assets, specs, plans, follow-ups, `uv.lock`, and this file.

**Deliberately not in git.** These are local, contain account-specific data, or are regenerable:

| Path | Why | On a new machine |
|---|---|---|
| `.venv/`, `venv/` | Environments | `uv sync` |
| `dist/`, `*.build/`, `*.dist/` | Build output | `uv run python -m tools.build_nuitka` |
| `.env`, `.env.local` | Credentials | Use `~/.aws` profiles |
| `aws_data.db`, `*_report.csv`, `*_report_summary.md`, `AWS_COST_*.md` | Legacy-tool account and cost data | Copy by hand if you need it. Never push it to a remote. |
| `.superpowers/` | Per-run scratch | Not needed |
| `.claude/settings.local.json` | Per-machine permissions | Not needed |
