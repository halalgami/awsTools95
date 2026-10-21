# CLAUDE.md

This file guides Claude Code (claude.ai/code) when working in this repository.

## Project overview

AWS 95 Tools is a native desktop explorer for multiple AWS accounts with a Windows 95 look: a desktop, taskbar, Start menu, MDI program windows, original sounds, screensavers, and Light and Dark themes. It is built with PySide6 (Qt Widgets) and runs on macOS and Windows.

Every window is live (3.1.0). Windows read only the local store `aws95.db`, which snapshots (labsnap engine: resumable sections over 22 collectors, 44 rules) and Activity loads (ctwatch engine: CloudTrail LookupEvents, Import History… from the trail's S3 files) fill. Only Take/Resume Snapshot, Update activity, Import History…, Verify, Sign In and AI Insights' Refresh models / Analyze reach AWS. Reports and Ctrl+K search are composed from the other providers. `--demo` shows watermarked sample data; `--offline` never touches AWS. Status, design decisions, the release recipe and the backlog are in `docs/PROJECT_CONTEXT.md`.

It is strictly **read-only** against AWS, with one named exception: AI Insights' `bedrock-runtime:Converse` (gate.INSIGHTS, VALIDATED by a strict request check), made only on Analyze with the reviewed aggregate payload; it writes nothing. Only `aws95/aws` imports boto3/botocore (lazily). Every AWS API call aws95 makes goes through `aws95.aws.gate`, which allows only the reviewed `(service, operation)` pairs in `gate.ALLOWED`; tests enforce it. SDK credential resolution (STS/SSO token refresh) is botocore's own and outside the gate. aws95 never writes `~/.aws` (the AWS CLI launched by Sign In writes its own `~/.aws/sso/cache`, and botocore may refresh its own SSO token cache while resolving credentials).

**Binding design specs:** one per feature in `docs/superpowers/specs/` (phase 1 UI revamp, 2a foundation, first drop, resumable snapshots, reports and search, billing complete, activity polish, findings tools, AI insights, edge and DNS collectors). A spec is the authority when code and docs disagree.

Milestones:
- **M1:** foundation and widget kit.
- **M2:** desktop shell.
- **M3:** the nine program windows and demo fixtures.
- **M4:** performance, accessibility, packaging (Nuitka, `tools/build_nuitka.py`, `docs/packaging.md`) and legacy removal. Phase 1 is complete.

Plans are in `docs/superpowers/plans/`. Rulings, deferred items and real-device checklists are in `docs/superpowers/followups/`. Both folders are private working notes and are never published (`tools/publish_check.EXCLUDED_DIRS`).

**Resuming (person or agent, any machine):** read `docs/PROJECT_CONTEXT.md` first: what works, where things are, how work is done, how to release, the backlog. The owner's private checkout also has `docs/superpowers/followups/MASTER-TODO.md` and the plans; they are never published and nothing in the public repo depends on them. Keep `PROJECT_CONTEXT.md` current at the end of each piece of work.

## Quick commands

```bash
uv sync                                  # install deps into .venv (Python 3.13, PySide6 6.10)
uv run python -m aws95                   # run the app live: boots with Accounts reading ~/.aws; --offline gives the all-NOT_CONNECTED set
uv run python -m aws95 --demo            # watermarked demo data in every program (dev only)
uv run python -m aws95 --offline         # never touch AWS (same as AWS95_OFFLINE=1); tests set it
uv run python -m aws95 --gallery         # widget-kit gallery for visual QA
./run.sh [--demo]                        # same as above, syncs deps first
open AWS95Tools.app                      # macOS launcher bundle (runs the package via uv)

uv run pytest -q                         # full suite, headless (offscreen Qt), ~70 s on the owner's Mac (2.4k tests)
uv run pytest tests/test_screenshots.py --update-screenshots   # regenerate visual goldens, then LOOK at them
uv run python -m tools.make_demo_fixtures   # regenerate demo fixtures (deterministic)
uv run python -m tools.make_sounds          # regenerate the original WAV sound bank
uv run python -m tools.make_icons           # regenerate app icons (PNG sizes, .ico, .icns)
```

## Architecture

```
aws95/
  app.py              boot: splash → theme → ShellWindow; restart loop (RESTART_CODE); app_icon()
  core/               Settings (typed QSettings), TaskRunner + CancelScope (QThreadPool), cancel tokens, paths
  kit/                Win95 widget kit, no AWS knowledge:
                        theme tokens (LIGHT/DARK), Win95Style (QProxyStyle), icons, mascot,
                        widgets (DataGrid, StateView, Inspector, charts, StatusStrip, Watermark, dialogs, …)
  contracts/          Qt-free frozen dataclasses: Result/Status/Coverage/Scope/Page + per-domain types
                        (the phase-2 seam)
  aws/                the only package that imports boto3/botocore (lazily): gate (ALLOWED pairs), errors, discover
                        (~/.aws profiles), session, identity, permissions (probes + policy JSON), sso (aws sso login)
  aws/engines/labsnap/ collectors (gate-only), rules, findings, diff, SnapshotService (resumable sections)
  aws/engines/ctwatch/ rules, event-scope table, LookupEvents loader, S3 import (validated), ActivityService
  aws/insights/       Bedrock list + Converse (validated), prompts, answer parsing (AI Insights)
  store/              aws95.db: one SQLite store (snapshots + sections (v2), findings, accepted findings, rule tuning and seeds (v3), activity),
                        exact decimal text
  providers/          base.py Protocols + ProviderSet
                        empty.py   NOT_CONNECTED for everything (--offline, and the not-yet-wired domains)
                        demo/      fixtures + demo providers (--demo only)
                        composed.py Reports and Search built from the other providers (demo and live share it)
                        live/      live providers (default): Accounts over aws/; Resources, Findings, Fleet, Snapshots,
                                   Billing, Activity over the store; Reports, Search composed; Insights over Bedrock
  shell/              ShellWindow, Desktop (QMdiArea), ProgramWindow, taskbar/tray, Start menu,
                        Ctrl+K palette, Alt+` switcher, snapping, sounds, screensaver/, splash, shutdown,
                        registry, snapshot_prompt (prompt, SnapshotController, Clear Local Data…),
                        activity_jobs (ActivityController), activity_dialogs (Load Activity History, Import History…)
  programs/           common/ (ProgramBase, ScopeBar, paging, export) and one package per program:
                        accounts, resources, billing, findings, fleet, snapshots, activity, insights, reports,
                        screensavers, plus display.py and recycle_bin.py.
                        Manifests: programs/__init__.py DEFAULT_MANIFESTS
  assets/             sounds/*.wav and icons/aws95-*.png, aws95.ico, aws95.icns (all generated, committed)
tools/                make_sounds.py, make_demo_fixtures.py, make_icons.py, build_nuitka.py
tests/                pytest + pytest-qt; screenshots/<platform>/ holds the visual goldens
docs/superpowers/     specs, plans, followups
docs/                 PROJECT_CONTEXT.md (portable status + working style), packaging.md
```

### Layer rules

These are enforced by `tests/test_layers.py`.

- **kit:** never imports shell, programs or providers. From contracts it imports only `aws95.contracts.common`.
- **programs:** never import `aws95.providers`. They get data only through `ctx.providers`. The allowed shell imports are `shell.context`, `shell.registry`, `shell.window`, `shell.screensaver` and `shell.sound`.
- **contracts:** no Qt and no boto3.
- **aws:** the only package that imports boto3/botocore, and only inside functions. Only `providers/live` imports `aws95.aws`; `import aws95.providers.live` must leave botocore out of `sys.modules`.

### How a program window works

Programs subclass `ProgramBase` (`aws95/programs/common/base.py`).

**Window layout:** menu bar (File/Edit/View/<program>/Help) → action toolbar → its own ScopeBar row (account × region) → body → StatusStrip (counts │ coverage │ freshness).

Every provider call goes through `ProgramBase.load(view, fn, ..., render=..., status=...)`, which runs on the TaskRunner. `load()` takes care of:
- dropping stale results per view (generation counter);
- cancelling when the window closes;
- watermarking the body when a result `is_demo`.

Rules every program follows:
- `invalidate(view)` before putting a view into a message state outside `load()`.
- Clear replaced content when a load starts. Old rows must never stay exportable.
- Exactly one load owns the status bar. Secondary and dialog loads pass `status=False`.
- Paged lists go through `programs/common/paging.fetch_all_pages`. It merges pages, keeps the most degraded status, merges coverage, and errors on a repeated cursor.
- Dialogs: non-modal, `show()` never `exec()`, take focus, Esc closes, and they are marked via `mark_dialog` (watermark plus `[DEMO]`).
- Exports catch OSError/ValueError and report in the status bar. CSV defangs formula cells. Markdown carries provenance.
- Cross-program links go through `ctx.shell.navigate(program_id, params)` → `apply_navigation`.

## Non-negotiable rules (built for regulated environments)

- **Money:**
  - Money is `Decimal` end to end.
  - A view never adds, subtracts, divides, rounds or estimates a provider figure.
  - Display goes through `format_value(x, "money")`: exact, never scientific.
- **Honesty (spec §9):**
  - A failure is never shown as empty.
  - Demo data is never shown without the watermark and `[DEMO]`.
  - Numbers come only from a `Result`.
- **Untrusted text:** a QLabel shows provider text only with `Qt.TextFormat.PlainText`.
- **Privacy:**
  - No customer, account or transaction data leaves the user's machine, except the read-only requests to AWS and AI Insights' reviewed, anonymised aggregates sent to Bedrock in the same account.
  - AI Insights sends exactly the payload the user reviewed (`analyze(scope, kind, payload)`).
  - Never log PII.
- **No AWS writes, ever.** Phase-2 providers use read-only APIs; AI Insights' Converse is the one billed non-read call (writes nothing). S3 reads exist only inside Import History…: `s3.list_objects_v2`/`s3.get_object` are `gate.VALIDATED` and refused outside `history.scoped(loc)`.
- **Store:** money and metric values are exact decimal TEXT (`aws95.store.dec_text`); sums use `aws95.contracts.common.exact_add/exact_sum`. Demo mode never writes the store.

## Qt lessons (learned the hard way)

- No long-lived signal lambdas capturing their own sender. Use bound methods or `functools.partial`.
- Guard possibly-deleted objects with `shiboken6.isValid`.
- Popup menus: `popup()` plus `aboutToHide → deleteLater`, never `exec()` on a fresh menu.
- Teardown: never `deleteLater` owned popups. Never call `QObject.thread()` on short-lived objects. Both were segfault causes.
- Never drop the last reference to a QObject inside a slot running in that object's own signal emission. Defer with `QTimer.singleShot(0, …)`.
- `StateView` starts in CONTENT. Tests wait on render evidence, never on state alone.
- macOS: `qt_set_sequence_auto_mnemonic(True)` is set at startup so Alt mnemonics work.

## Git workflow

- **From the public repo** (`https://github.com/halalgami/awsTools95`): branch `feature/<topic>` off `main`, keep PRs small. Never commit to `main` directly.
- **In the owner's private checkout:** the development branch is `feature/ui-revamp-spec` (local `main` is an old pre-revamp baseline: never commit or merge into it). Feature branches come off `feature/ui-revamp-spec`. Releases are published from the local `public` branch, one scrubbed commit per release, pushed as `main`; the recipe is in `docs/PROJECT_CONTEXT.md` § Releasing. Every merge and push needs the owner's explicit OK at that moment.
- One task, one branch. Conventional commits, ending with a `Co-Authored-By: Claude <model> <noreply@anthropic.com>` trailer.
- `tests/test_publish_check.py` runs `tools/publish_check.py` on every test run. It fails on non-placeholder account IDs, real-looking emails, AWS keys, organisation names (stored as digests), and tracked data files. Never weaken an allow-list to make it pass: fix the content, or add one exact `ALLOWED_OCCURRENCES` entry with a reason.
- Never commit the gitignored data files (`aws95.db`, `aws_data.db`, report CSVs, `AWS_COST_*.md`, `.env`); they hold account-specific data. Use the placeholder account ids (`123456789012`, `210987654321`, repdigits) in tests and docs.

## Legacy code

All phase-1 legacy files are gone (2b). `tests/test_layers.py` guards against their return. The launchers (`AWS95Tools.app`, `.command`, `.bat`, `run.sh`) run the `aws95` package.
