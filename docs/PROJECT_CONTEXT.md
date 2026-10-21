# Project context

Everything a person or a coding agent needs to pick up AWS 95 Tools from a fresh clone. Read this first, then
[`CLAUDE.md`](../CLAUDE.md) (the rules every change must follow). Last updated 2026-10-21 (version 3.1.0).

## In one paragraph

AWS 95 Tools is a native desktop explorer for several AWS accounts, dressed as Windows 95 (PySide6 / Qt Widgets,
macOS and Windows). It is read-only against AWS: every call goes through one allow-list (`aws95/aws/gate.py`), with a
single named exception (AI Insights' Bedrock `Converse`, only on an explicit Analyze). Windows read a local SQLite
store (`aws95.db`) that snapshots and Activity loads fill; opening a window never calls AWS. Money is exact `Decimal`,
failures are never shown as empty, and demo data is always watermarked.

## Start here

```bash
git clone https://github.com/halalgami/awsTools95.git && cd awsTools95
uv sync                                   # Python 3.11–3.13, PySide6 6.10
uv run python -m aws95 --demo             # watermarked sample data, no AWS
uv run python -m aws95 --offline          # never touches AWS
uv run python -m aws95                    # live: reads your ~/.aws profiles (setup wizard on first run)
uv run pytest -q -W error                 # ~2.4k tests, headless, ~70 s; must stay green and warning-free
uv run python -m tools.publish_check      # the leak check (also runs inside the suite)
```

A coding agent should read `CLAUDE.md` (Claude Code loads it automatically; `AGENTS.md` points other agents to it).

## Status (3.1.0)

Every window is live. Nothing is half-built.

| Area | What works | Design spec (`docs/superpowers/specs/`) |
|---|---|---|
| Shell | Desktop, taskbar, Start menu, MDI windows, Ctrl+K palette (also searches local data), Alt+\` switcher, snapping, sounds, screensavers, Light/Dark, 100–150 % fonts, Take Snapshot desktop icon | `2026-10-02-aws95-ui-revamp-design.md` |
| My Accounts, setup wizard | Profiles from `~/.aws`, verification, permission probes, SSO Sign In, nicknames/colours/regions, Billing opt-in per account | `2026-10-05-phase2a-foundation-accounts-design.md` |
| Snapshots | 19 collectors, resumable sections (account × collector × region) with a Run tab, Resume, diff, trends | `2026-10-07-first-drop-design.md`, `2026-10-16-resumable-snapshots-design.md` |
| Resources | Types and rows from the latest snapshot; inspector with Attributes, Tags, Raw JSON and **Related** (stored CloudTrail events naming the resource) | first drop, `2026-10-20-activity-ui-polish-design.md` |
| Findings, Fleet | 38 rules, open/resolved/accepted, local Accept, **Rules…** tuning, **Seed**, **Backfill**; rule × account matrix with a cost row | first drop, `2026-10-20-findings-tools-design.md` |
| Billing | Summary, by service/account/region/usage type, trend, daily, free tier, uncovered spend (Cost Explorer, opt-in, ≈ $0.06 per account per snapshot) | first drop, `2026-10-18-billing-complete-design.md` |
| Activity Log | CloudTrail LookupEvents in the background (newest first, resumable), Import History… from the trail's S3 files, Overview/Timeline/Watchlist/Filter | first drop |
| Reports, Search | Four reports with a Coverage block; Ctrl+K finds resources, findings, principals and services | `2026-10-18-reports-search-design.md` |
| AI Insights | Per-account anonymised aggregate preview; model per account; one Bedrock Converse per account on Analyze; export | `2026-10-20-ai-insights-design.md` |

## Where things are

| What | Where |
|---|---|
| Rules for every change (layers, money, honesty, Qt lessons, git) | `CLAUDE.md` |
| Architecture map | `CLAUDE.md` § Architecture |
| Design specs (binding; one per feature) | `docs/superpowers/specs/` |
| The AWS allow-list and its tests | `aws95/aws/gate.py`, `tests/test_aws_gate.py`, `tests/test_engine_calls.py` |
| IAM policy, README cost table (generated) | `aws95/aws/permissions.py`, `tools/make_readme_policy.py` |
| Leak check (ids, keys, emails, org names) | `tools/publish_check.py`, `tests/test_publish_check.py` |
| Visual goldens | `tests/screenshots/<platform>/` (regenerate with `--update-screenshots`, then look at every PNG) |
| README screenshots | `tools/make_readme_screenshots.py` → `docs/screenshots/` (sample data only) |
| Packaging | `tools/build_nuitka.py`, `docs/packaging.md` |

The owner also keeps private plans and notes (`docs/superpowers/plans/`, `docs/superpowers/followups/`: private, never published).
Everything needed to continue is in this file, `CLAUDE.md` and the specs.

## How work is done here

1. **Brainstorm → spec → plan → build.** A new feature gets a short design discussion, a written spec in
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, an implementation plan (tasks with tests first), then the
   build. Small fixes skip the spec but still go test-first.
2. **Test-first.** Write the failing test, watch it fail, make it pass, run the whole suite (`-W error`).
3. **One task, one branch** (`feature/<topic>`), small commits (conventional style). Never commit to `main` directly.
4. **Look at the UI.** Every visible change gets Light, Dark and Dark-150 % goldens; open each PNG. Dark mode and large
   fonts are rated Important, never polish.
5. **Rulings.** When a plan meets reality, decide, then record `Ruling: <what> — <why> — <cost if wrong>` in the
   change's notes so the owner can see every call made on their behalf.
6. **Merges and pushes need the owner's OK** at that moment.

## Releasing

The public repository holds one commit per release on `main`, built from the development tree minus the private
folders, under the owner's public identity (name `halalgami`, the account's GitHub noreply email). From the owner's
development checkout (branch `public` tracks the published history):

```bash
uv run pytest -q -W error && uv run python -m tools.publish_check     # both green first
SRC=<the development branch>
TMP="$(mktemp -d)"; export GIT_INDEX_FILE="$TMP/index"
git read-tree "$SRC"
git rm -r --cached --quiet --ignore-unmatch -- docs/superpowers/plans docs/superpowers/followups   # the private folders
TREE="$(git write-tree)"; unset GIT_INDEX_FILE
PUBLIC_EMAIL=<the halalgami account's GitHub noreply email>
COMMIT="$(GIT_AUTHOR_NAME=halalgami GIT_AUTHOR_EMAIL="$PUBLIC_EMAIL" \
          GIT_COMMITTER_NAME=halalgami GIT_COMMITTER_EMAIL="$PUBLIC_EMAIL" \
          git commit-tree "$TREE" -p public -m "AWS 95 Tools <version>")"
git branch -f public "$COMMIT"
uv run python -m tools.publish_check --commit public                    # files and commit identity
git push origin refs/heads/public:refs/heads/main                       # only with the owner's OK
```

Bump `aws95/__init__.py` `__version__` (and `AWS95Tools.app/Contents/Info.plist`) before building the release.

## Backlog

- **Release track:** macOS signing and notarisation, a Windows packaged-build test, auto-update. Each needs its own
  spec. (The owner has said these are not wanted for now.)
- **Known minors:** live screenshot goldens depend on test order (an earlier test sets account nicknames); one rare,
  unreproduced suite failure seen in ~20 runs; the AI Insights results show raw model ids; the IAM inference-profile
  pattern assumes profile ids contain `anthropic.`.
- **Ideas recorded, not scheduled:** custom rule folders (needs a security review: it would run code from outside the
  package), scheduled snapshots, more collectors for services that show up under Billing ▸ Uncovered spend.

## Design decisions

- **Platform:** native desktop only (PySide6 Qt Widgets), macOS and Windows; speed from model/view, cache-first
  loading, background workers and Nuitka builds.
- **Look:** authentic grey Win95 chrome with modern fonts and spacing; a graphite Dark twin; font scale 100–150 %.
- **Layout:** the full Win95 desktop metaphor (teal desktop, icons, Start menu, taskbar, floating windows).
- **Whimsy is a feature:** original sounds, screensavers dismissed by an explicit click, the Cloudy mascot — all
  toggleable, none allowed to cost speed. No Microsoft or Amazon artwork.
- **Accounts:** named profiles only (keys, SSO, `role_arn` + `source_profile`); live read-only probes for permissions;
  SSO Sign In runs `aws sso login` and shows the code in-app.
- **Activity history:** CloudTrail LookupEvents plus the trail's own S3 files; no Athena.
- **Cost transparency:** every billed action states its cost before it runs (constants in `aws95/contracts/`).

## Not in git (local only)

| Path | Why | On a new machine |
|---|---|---|
| `.venv/` | Environment | `uv sync` |
| `dist/`, `*.build/`, `*.dist/` | Build output | `uv run python -m tools.build_nuitka` |
| `aws95.db` (in the app data folder) | Your accounts' data | Created on first snapshot; never commit it |
| `.superpowers/`, `.claude/settings.local.json` | Per-run / per-machine scratch | Not needed |
