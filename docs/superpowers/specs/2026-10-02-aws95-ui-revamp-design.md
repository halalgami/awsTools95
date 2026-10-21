# AWS 95 Tools — UI Revamp (Phase 1) Design

**Date:** 2026-10-02
**Status:** Phase 1 complete (2026-10-04). Approved in brainstorming; close-out in `docs/superpowers/followups/2026-10-04-m4-followups.md` (private; not in the public repo).
**Branch:** `feature/ui-revamp-spec`
**Supersedes:** the current PySide6 UI (`ui/`, `widgets/`, `resources/synthwave.qss`) and the legacy Tkinter UI

---

## 1. Intent

AWS 95 Tools becomes the owner's single desktop tool for examining, deep-diving and exploring
**multiple AWS accounts**. It also absorbs the capabilities of the original labsnap/ctwatch tools:

- **ctwatch:** CloudTrail activity, i.e. *who did what*.
- **labsnap:** account state, i.e. *what exists, what changed, what it costs, what is still broken*.

The work is split into two phases:

| Phase | What | This spec |
|---|---|---|
| **1** | Build **one cohesive UI** with a designed home for every feature: the current app's, ctwatch's and labsnap's. Windows are fully functional *as UI* against typed data contracts, but are **not wired to AWS engines**. | **Yes** |
| **2** | Import the engines and write adapters that fill the contracts. | Outline only (Appendix A) |

### What the owner said

- Full revamp; "blow up the old UI".
- Native desktop, **no web tech**, as fast as possible, macOS and Windows.
- Win95 vibes with a modern twist, and modern conveniences: easier and less clunky navigation, better engineering and UX.
- Look: **Classic+** (authentic grey Win95), plus a **dark mode** ("my eyes aren't young anymore").
- Layout: the **full Win95 Desktop metaphor**: "go all in and take me back to my childhood".
- Fun and whimsy: Win95-style sound effects and a launch jingle.
- Idle **screensavers**: one random saver each time the app goes idle, only one running, dismissed only by clicking a button.
- Auth: **named profiles** now. SSO, assume-role and Organizations are stubbed for later.
- **Strictly read-only** against AWS.
- Costs: **Cost Explorer actuals only**. No estimates.
- Before wiring: **empty states**, plus a **dev-only demo mode** that is always watermarked.

### Assumptions

- Single user, running locally on their own machine against their own accounts. No multi-user, server, or sync.
- Python 3.11+ and PySide6 6.10 (what the current venv has).
- Governance for regulated environments applies: no estimated financial figures, least privilege, no customer data leaving the machine, branch-per-task, never commit to `main` after the baseline.

### Success criteria

1. Cold start to an interactive desktop in **under 1 s** (packaged build, warm disk). The splash appears in **under 300 ms**.
2. Nothing ever blocks the GUI thread. Window drag, resize and scroll stay smooth while background tasks run.
3. Every program reachable from the Start menu, the desktop and Ctrl+K, and fully operable from the keyboard.
4. Light and Dark themes switch live, and both meet **WCAG AA** text contrast.
5. Every ctwatch and labsnap capability listed in §7 has a designed, navigable home.
6. No window ever shows an invented figure: either a real provider (phase 2), an honest empty state, or watermarked demo data.

---

## 2. Decisions

| # | Decision | Alternatives rejected |
|---|---|---|
| D1 | **PySide6 Qt Widgets, rebuilt from scratch** | Qt Quick/QML (JS logic, weaker desktop tables); Dear PyGui (no native dialogs, weak text); Rust UI + Python sidecar (two languages, IPC) |
| D2 | **Classic+ visual style**: grey `#c0c0c0` chrome, navy→blue title gradient, real bevels, modern font and spacing, zebra rows | Midnight 95 (dark only), Vapor 95 (neon) |
| D3 | **Classic+ Dark** twin: same geometry, graphite surfaces, amber money figures. Follow-OS with manual override | Light only |
| D4 | **Desktop metaphor** with modern window management (snap, tile, switcher, remembered geometry, palette) | Explorer, Hybrid |
| D5 | **Contracts + providers seam.** UI depends only on `aws95.contracts`. Phase 2 swaps providers | UI talking to engines directly |
| D6 | **Empty provider by default. Demo provider dev-only and always watermarked** | Unlabelled sample data (the current behaviour, which is removed) |
| D7 | **Original** sounds, jingle, screensavers and mascot | Microsoft or After Dark assets, which are copyrighted |
| D8 | Read-only, no credential storage, named profiles only (others stubbed) | Gated write actions |

---

## 3. Scope

### In scope (phase 1)

- New `aws95/` package: widget kit, theme and style, shell, all program windows, contracts, empty and demo providers, settings.
- Original sound bank and launch jingle, generated reproducibly by `tools/make_sounds.py`.
- Five original screensavers.
- Demo fixtures for every program.
- Tests (pytest-qt), screenshot tests, a startup benchmark.
- `pyproject.toml` (uv), Nuitka build scripts for macOS `.app` and Windows `.exe`.
- Git hygiene: repo initialised, baseline on `main`, all work on `feature/ui-revamp-*` branches.

### Removed

- `ui/`, `widgets/`, `resources/synthwave.qss`, `win95_gui.py`, `main.py`, `aws_collector.py`, `qt_compat.py`, the empty `aws95tools.db`, `data/sample_data.py`.
- `AWS95Tools.app`, `.bat` and `.command` launchers, replaced by Nuitka builds and a new `run.sh`.
- Behaviours: sample-data mode; per-resource cost estimates; fake Start/Stop/Terminate actions.

### Left untouched until phase 2

- `core/aws_data_collector.py`, `core/bedrock_analyzer.py`, `core/async_worker.py`, `database.py`, `credentials_handler.py`, `config.py`.
- `lambda_report.py`, `s3_report.py`.

During phase 1 nothing imports them. Phase 2 replaces or absorbs them.

### Non-goals

- Wiring any AWS engine (phase 2).
- Any AWS write or remediation.
- SSO, assume-role and Organizations auth: UI stubs only.
- Minesweeper, Notepad and other accessories (possible later; the Start-menu *Accessories* folder exists empty).
- Web, server, or multi-user anything.

---

## 4. Architecture

### 4.1 Package layout

```
aws95/
  __init__.py            # __version__ (single source)
  __main__.py            # python -m aws95
  app.py                 # QApplication bootstrap: splash → theme → shell; crash handler
  core/
    settings.py          # typed QSettings wrapper (theme, sounds, saver, geometry, accounts, font scale)
    tasks.py             # TaskRunner: QThreadPool + QRunnable, cancel tokens, queued result signals
    paths.py             # per-OS config/cache dirs (0700)
  kit/                   # Win95 widget kit: no AWS knowledge, reusable
    theme.py             # Theme tokens (Light, Dark), OS-follow, live switch signal
    style.py             # Win95Style(QProxyStyle over Fusion): bevels, buttons, title bars,
                         #   scrollbars, tabs, focus rects, checkboxes, progress blocks
    qss.py               # tiny QSS for fonts/spacing only
    icons.py             # icon registry (bundled 32x32 / 16x16 pixel-style PNGs)
    widgets/
      bevel.py           # BevelPanel, SunkenWell, GroupBox95
      buttons.py         # Win95Button, ToolButton95, Chip (account colour chip)
      datagrid.py        # DataGrid: QTableView + QSortFilterProxyModel + FilterSyntax
      filter_syntax.py   # parser: `state:stopped region:eu-* name~web -tag:temp`
      inspector.py       # tabbed detail pane (Attributes / Tags / Raw JSON / Related)
      stateview.py       # StateView: loading | ok | empty | not_connected | error (denied/partial)
      watermark.py       # diagonal "DEMO DATA" overlay
      balloon.py         # tray balloon notification
      progress.py        # block-style progress bar + indeterminate marquee
      charts.py          # pyqtgraph wrappers themed by tokens (bar, line, stacked)
      jsonview.py        # read-only pretty JSON tree/text viewer
      dialogs.py         # MessageBox95 (info/warn/error/question with classic icons)
  shell/
    desktop.py           # Desktop(QMdiArea): wallpaper, icon grid, snapping, tile/cascade
    window.py            # ProgramWindow(QMdiSubWindow): title bar, account chip, geometry memory
    snapping.py          # edge detection + dashed preview overlay + keyboard snap
    startmenu.py         # Start menu + cascading submenus, generated from registry
    taskbar.py           # Start button, window buttons, tray
    tray.py              # account switcher, volume/mute, job progress, clock, balloons
    palette.py           # Ctrl+K fuzzy command palette
    switcher.py          # Alt+` window switcher
    registry.py          # ProgramManifest + Registry (single source for menu/icons/palette/help)
    sound.py             # SoundBank: QSoundEffect per event, master volume, per-event toggles, mute
    splash.py            # boot splash + jingle
    shutdown.py          # Shut Down… dialog + CRT power-off animation
    mascot.py            # "Cloudy": empty-state art + optional tips
    screensaver/
      manager.py         # idle detection, random pick per idle session, multi-screen cover, Resume dialog
      base.py            # Saver base (QWidget, paintEvent + QTimer @ display refresh)
      starfield.py mystify.py pipes.py maze.py bounce.py
  contracts/             # the phase-2 seam (pure dataclasses + Protocols, no Qt)
    common.py            # Status, Coverage, Page, AccountRef, Freshness
    accounts.py resources.py billing.py findings.py snapshots.py activity.py insights.py reports.py
  providers/
    base.py              # ProviderSet: one provider per domain
    empty.py             # returns NotConnected for everything
    demo/
      __init__.py        # DemoProviderSet: loads fixtures, sets is_demo=True
      fixtures/*.json    # hand-authored, schema-validated fake data
  programs/
    accounts/ resources/ billing/ findings/ snapshots/ fleet/ activity/ insights/ reports/ display/
      manifest.py        # ProgramManifest
      window.py          # the ProgramWindow content
      models.py          # Qt models adapting contracts → views
tools/
  make_sounds.py         # synthesises original WAVs (numpy) → aws95/assets/sounds/
  make_icons.py          # renders pixel icons / app icon
  build_nuitka.py        # per-OS packaging
tests/
```

### 4.2 Layer rules

- `kit` imports nothing from `shell`, `programs` or `providers`; from `contracts` it may import only `aws95.contracts.common` (Qt-free value types used by `StateView.apply_result`).
- `programs` import `kit`, `shell.window`, `contracts` and `core`. They **never** import `providers` directly. They receive a `ProviderSet` through the `ProgramContext`.
- `contracts` has no Qt and no boto3 dependency. These modules are what phase-2 engine adapters target.
- Only `app.py` chooses the provider set: Empty by default, Demo when `--demo` or `AWS95_DEMO=1` is given.

### 4.3 Program registry

```python
@dataclass(frozen=True)
class ProgramManifest:
    id: str                      # "activity"
    title: str                   # "Activity Log"
    icon: str                    # icon registry key
    category: Literal["programs", "accessories", "settings", "system"]
    desktop_icon: bool
    shortcut: str | None         # "Ctrl+Shift+L"
    instancing: Literal["singleton", "multi"]
    factory: Callable[[ProgramContext], QWidget]   # imported lazily
    keywords: tuple[str, ...]    # palette search terms
```

The Start menu, desktop icons, Ctrl+K palette, taskbar and Help → Keyboard Shortcuts are all
**generated** from the registry. A new program needs one folder and no shell edits. Factories are
import strings resolved on first open, so start-up imports only the shell.

### 4.4 Tasks and threading

- `TaskRunner.submit(fn, *args, cancel=token) -> TaskHandle`. Results, progress and errors are delivered by queued signals on the GUI thread.
- Every `ProgramWindow` owns a `CancelScope`. Closing the window cancels its tasks, and late results are dropped.
- The tray job indicator aggregates running tasks: count, plus an indeterminate block progress bar.
- **Rule: no provider call, file I/O larger than 1 MB, or JSON parse of a fixture runs on the GUI thread.** A debug-build watchdog logs any GUI-thread stall over 50 ms.

### 4.5 Performance budgets

| Metric | Budget |
|---|---|
| Splash visible | ≤ 300 ms |
| Desktop interactive (packaged, warm) | ≤ 1.0 s |
| First open of a program window | ≤ 150 ms to frame, data async |
| DataGrid with 100k rows | 60 fps scroll; filter keystroke → result ≤ 100 ms |
| Theme switch | ≤ 200 ms, no restart |
| Idle CPU (desktop, no saver) | ~0% (no timers except clock @ 1/min) |
| Screensaver | ≤ 1 core at 60 fps; pauses when the window is hidden |

Techniques:
- Model/view throughout, with `setUniformRowHeights` and incremental `fetchMore`.
- No per-cell widgets.
- Cached `QPixmap` bevel tiles.
- Lazy program imports.
- Fixtures parsed off-thread.
- pyqtgraph with `useOpenGL` where available.

---

## 5. Visual system

### 5.1 Tokens

The two themes share every geometry and differ only in tokens:

| Token | Light (Classic+) | Dark (Classic+ Dark) |
|---|---|---|
| face / face2 | `#c0c0c0` / `#d4d4d4` | `#2d2f36` / `#383b43` |
| bevel hi / lo / dark | `#ffffff` / `#808080` / `#404040` | `#4f5360` / `#17181c` / `#0e0f12` |
| well (inputs, grids) | `#ffffff` | `#1d1e23` |
| zebra | `#f2f2f7` | `#23252b` |
| text / muted | `#000000` / `#444444` | `#e3e5ec` / `#9a9fb0` |
| selection / text | `#000080` / `#ffffff` | `#2f5fa8` / `#ffffff` |
| title active | `#000080 → #1084d0` | `#1a2a66 → #2f6fb3` |
| title inactive | `#808080 → #b5b5b5` | `#3a3d46 → #4a4e59` |
| desktop | `#008080` | `#123c40` |
| money | text colour, bold | `#ffcc73`, bold |
| ok / warn / bad | `#00a000` / `#c88000` / `#b00000` | `#5fd38d` / `#ffc46b` / `#ff7b7b` |

Light muted was raised from #555555 to #444444 during M1 planning: #555555 on #c0c0c0 is 4.09:1, below AA.

All text/background pairs are checked against WCAG AA in a unit test.

### 5.2 Typography and scale

- UI font: system sans (Segoe UI on Windows, SF on macOS), 9 pt base.
- Monospace: system mono, for JSON and ARNs.
- **Font scale 100–150%** in Display Properties. All metrics derive from it.
- Tabular numerals for every number column.

### 5.3 Style implementation

`Win95Style(QProxyStyle)` paints the following:
- raised and sunken bevels
- default-button outlines
- dotted focus rectangles
- Win95 scrollbars (arrow buttons, checkered track)
- tabs, checkboxes and radios
- block progress bars
- MDI title bars with ▁ □ ✕ buttons

All painting is driven by tokens. QSS is limited to font and padding. On macOS the native menu bar
is **off** by default, because menus live in each window as in Win95 (`AA_DontUseNativeMenuBar`). A setting turns it on.

---

## 6. Shell

### 6.1 Boot and shutdown

1. The splash shows the AWS95 logo, a sliding block progress bar, and "Starting AWS 95…" while the shell builds.
2. The desktop fades in and the **original launch jingle** plays (≤ 3.5 s).
3. Settings: *Skip splash*, *Play startup sound*.
4. **Shut Down…** (Start menu or Ctrl+Q) opens the classic dialog: *Shut down AWS 95* or *Restart AWS 95*. Choosing one plays the shutdown sound and a CRT power-off animation (skippable, off when *Reduce motion* is set), then quits.

### 6.2 Desktop

- Wallpaper colour token, with an optional tiled pattern (Display Properties → Background).
- Desktop icons: My Accounts, Resources, Billing, Findings, Activity Log, Snapshots, Reports, Recycle Bin.
- **Recycle Bin** is whimsy: it lists closed windows from this session so you can reopen them.
- Icon behaviour: single-click selects, double-click or Enter opens, arrow keys move between icons.
- Icon positions are user-arrangeable and persisted. Right-click → *Arrange Icons*.

### 6.3 Windows

- `ProgramWindow`: an active title bar uses the gradient, an inactive one is grey.
- Title shows `icon · title · [account chip]`, plus `[DEMO]` in demo mode.
- Double-click the title bar to maximize. Minimize sends the window to the taskbar.
- **Snapping:**
  - drag to the left or right edge → half; a corner → quarter; the top → maximize
  - a dashed outline previews the snap
  - keyboard: Ctrl+Alt+←/→/↑/↓
- Taskbar right-click: *Tile horizontally*, *Tile vertically*, *Cascade*, *Minimize all*.
- **Alt+\`** window switcher (Alt+Tab belongs to the OS): a centred icon strip in classic style.
- Geometry is remembered per program id, and per instance slot for multi-instance programs.
- Windows can be multi-instance where the manifest allows it (Resources, Activity Log), so two accounts can sit side by side.

### 6.4 Start menu and taskbar

- Start menu:
  - a vertical **AWS95** stripe
  - *Programs ▸* (generated), *Accessories ▸* (empty for now), *Documents ▸* (recent reports and exports), *Settings ▸* (Display Properties, My Accounts, Sounds)
  - *Find… Ctrl+K*, *Help*, *Run…* (an alias of the palette)
  - separator, then *Switch Account…*, *Shut Down…*
- Opened by clicking Start, the Win/Meta key (where the OS allows it) or Ctrl+Esc. Fully keyboard-navigable.
- Taskbar: the Start button, one button per open window (icon + title + chip colour stripe), and the tray.
- **Tray:**
  - account switcher (active-account nickname ▾)
  - speaker: click to mute, double-click for volume
  - job progress: block progress bar, tooltip lists running tasks
  - clock
- Balloon notifications rise from the tray and fade on their own after 6 s.

### 6.5 Ctrl+K palette

- A fuzzy matcher over: programs, commands (theme, mute, tile, cascade, screensaver preview, shut down), accounts ("switch to …"), and recent windows.
- In phase 2 the matcher also covers resources, principals and findings, through a `SearchProvider` contract that exists from phase 1 and returns nothing until then.
- Results are grouped, keyboard-first, and show the shortcut hint next to each item.

### 6.6 Accounts in the shell

- Each account has a **nickname** and **colour**, set in My Accounts.
- The colour chip appears on window titles, taskbar buttons and the tray.
- Windows scoped to all accounts show a multi-colour chip.
- The "active account" in the tray is the default scope for newly opened windows. Already-open windows keep their own scope.

### 6.7 Sound

- `SoundBank` preloads `QSoundEffect`s at start-up, off-thread.
- Events: `startup`, `shutdown`, `window_open`, `window_close`, `minimize`, `maximize`, `menu_popup`, `click` (off by default), `balloon`, `error`, `question`, `refresh_done`, `saver_start`.
- Display Properties → Sounds: master volume, per-event toggle, a *Preview* button per event, and a global mute that the tray mirrors.
- **All audio is original**, synthesised by `tools/make_sounds.py` (numpy: FM bells, square-wave chimes, a short chord-based jingle, all designed to evoke 90s system sounds). The generated WAVs are committed (16-bit 44.1 kHz, total under 1 MB), and regeneration is deterministic.

### 6.8 Screensaver

- Idle detection uses an app-level event filter on input events. Timeout is set in Display Properties (1–60 min, default 10, or *None*).
- On idle: **one saver picked at random** from the five. It runs until dismissed; there is no rotation within a session. The next idle session picks again.
- Savers (original implementations of classic concepts): **Starfield**, **Mystify**, **Pipes**, **Maze Walk**, **Bouncing Logo** (the AWS95 wordmark).
- It covers **every screen** with frameless top-level windows, and all program content is hidden behind it.
- Any input only reveals a centred Win95 dialog: *"🔒 AWS 95 is idle. Account data hidden. [Resume]"*. **Only clicking Resume, or pressing Enter/Space while Resume has focus, dismisses it.** The dialog hides itself again after 10 s without input.
- It never starts while a modal dialog is open or while a long task reports *blocking*.
- Display Properties → Screen Saver has a dropdown (*Random* or a specific saver), the timeout, and *Preview*. The preview plays the saver live inside a drawn CRT monitor, as in the original dialog.
- Savers are `QWidget`s with `paintEvent` driven by a `QTimer` at the display refresh rate. They pause when occluded.

### 6.9 Mascot "Cloudy"

- An original character: a floppy disk wearing a small cloud.
- Appears in empty and not-connected states with a one-line, context-relevant message.
- Optional "Tip of the day" on start-up, off by default after first run.
- **Never pops up uninvited** during work.

---

## 7. Programs (phase-1 UI)

Every window shares one skeleton:

```
Title bar (icon · title · account chip · [DEMO])
Menu bar (File · Edit · View · <program> · Help)
Toolbar (Refresh · Export · scope pickers · filter box)
Body
Status bar: <counts> │ <coverage: regions covered/requested, denied/partial> │ <freshness: "Synced 2m ago" | "Not connected">
```

Every data region is wrapped in a `StateView`.

| Program | Feature home (source) | Body |
|---|---|---|
| **My Accounts** | shared | Profile list (from `~/.aws/config`, read through the provider). Detail pane: nickname, colour, default regions, *Verify* (STS identity), and per-collector health and preflight (labsnap `preflight`). Greyed "Coming soon": SSO, assume-role, Organizations. |
| **Resources** | labsnap collectors (19 types), current app | Left tree: resource types grouped by domain (Compute, Storage, Database, Network, Security, Identity, Containers, Messaging, …), with counts. DataGrid with filter syntax and column chooser. Inspector tabs: *Attributes*, *Tags*, *Raw JSON*, *Related activity* (phase 2, from ctwatch). Row badges: ⚠ partial, 🔒 denied. Scope: accounts × regions. |
| **Billing** | labsnap `cost`/`freetier`, current app | KPI strip: MTD, forecast, last month. Tabs: By service / By account / By region / By usage type / Trend (13 months) / Daily / Free tier / **Uncovered spend** (services with no collector). Double-click a service → Resources filtered to it. **Actuals only.** |
| **Findings** | labsnap rules and lifecycle | Severity chips (critical/high/medium/low). Tabs *Open / Resolved / Accepted (muted)*. Grid columns: severity, rule, target, account, **age**, first seen (seeded dates marked). Dialogs: *Mute…* (scope: rule / account / resource, reason, expiry defaulting to 90 days), *Rules…* (enable, severity override, params), *Seed finding…*, *Backfill…*. |
| **Snapshots** | labsnap snapshots / diff / trend | Snapshot history per account, with status and collector health. **Diff window** with tabs *Added / Removed / Changed / Excluded*. Excluded always lists the reason: collector failed, region not covered, partial data. *Trends*: metric picker plus chart, showing latest observation per period, with restatements marked. |
| **Fleet** | labsnap fleet | Rule × account matrix (✓ / ✗ / count). A cost row per account. Click a cell → Findings filtered to it. |
| **Activity Log** | ctwatch | Tabs: **Overview** (events over time, top principals, top actions and services, human vs service; range 24h/7d/30d/90d/custom), **Timeline** (principal list with counts and a human flag → chronological events), **Watchlist** (risk ≥ 1, sorted by severity then time), **Filter** (principal, service, action, date range, ARN substring, human-only, risk-only → Export MD/CSV). Event JSON viewer dialog. *Deep history (Athena)…* dialog with setup status. Scope: account(s) × region(s). |
| **AI Insights** | current app (Bedrock) | Analysis picker (insights / oddities / improvements / advice), a **"What will be sent"** preview showing aggregates only, results pane. |
| **Reports** | labsnap report, ctwatch export, current CSV export | Report type plus scope → preview → *Save as MD/CSV*. Saved files are listed under Start → Documents. |
| **Display Properties** | shell | The classic tabbed dialog: *Background*, *Screen Saver*, *Appearance* (Light / Dark / Follow OS, font scale, reduce motion), *Sounds*. |

---

## 8. Contracts (the phase-2 seam)

Plain frozen dataclasses with no Qt. Field sets mirror labsnap (`model.py`, `store/schema.py`) and
ctwatch (`store/cache.py`, `ingest/normalize.py`, `rules.py`), so phase-2 adapters are mechanical.

```python
# common.py
class Status(Enum): OK, PARTIAL, DENIED, UNREACHABLE, STALE, NOT_CONNECTED, ERROR
@dataclass(frozen=True) class AccountRef: account_id: str; nickname: str; color: str; profile: str
@dataclass(frozen=True) class Coverage: regions_requested: tuple[str,...]; regions_covered: tuple[str,...]; denials: int
@dataclass(frozen=True) class Freshness: observed_at: datetime | None; source: str   # "snapshot 2026-10-02T09:00Z"
@dataclass(frozen=True) class Result(Generic[T]): status: Status; data: T | None; coverage: Coverage | None;
                                                  freshness: Freshness | None; message: str | None; is_demo: bool

# accounts.py
Profile(name, kind: Literal["profile","sso","assume_role"], region, verified: AccountRef | None)
CollectorHealth(collector, status, error, duration_ms, coverage)
# resources.py
ResourceType(rtype, domain, label, count)
ResourceRow(account, rtype, region, rid, name, state, attrs: Mapping, tags: Mapping, partial: bool)
# billing.py
CostPoint(period, amount: Decimal, unit, dims: Mapping)       # Decimal, never float
CostSummary(mtd, forecast, last_month: Decimal | None, currency, as_of)
FreeTierUsage(service, metric, actual, limit, forecast, period)
# findings.py
Finding(account, rule, rtype, region, rid, severity, detail, first_seen, seeded: bool,
        state: Literal["open","resolved","accepted"], age_days, mute: Mute | None)
Mute(id, account | None, rule, rid | None, reason, muted_at, expires_at)
RuleInfo(id, title, description, severity, params, source, enabled)
# snapshots.py
Snapshot(id, account, caller_arn, taken_at, tool_version, status, collectors: tuple[CollectorHealth,...])
DiffEntry(kind: Literal["added","removed","changed","excluded"], rtype, region, rid, deltas, reason)
MetricPoint(name, dims, period, value: Decimal, unit, observed_at, restated: bool)
# activity.py
ActivityEvent(event_id, account, time_utc, principal_type, principal_name, is_human,
              source, name, read_only, source_ip, user_agent, resource_arns, region,
              risk_level: int, risk_rule: str | None)            # raw_json fetched on demand
PrincipalSummary(principal_type, principal_name, is_human, count)
Bucketed(series: tuple[(datetime, int), ...], bucket: Literal["hour","day"])
```

**Provider Protocols** (one per domain). Each method returns `Result[...]`, is called only through
`TaskRunner`, and accepts a `Scope(accounts, regions, time_range)` and a cancel token.
List methods return `Page` (cursor-based) so phase-2 engines can stream large tables.

- `EmptyProviderSet` returns `Result(status=NOT_CONNECTED, message="… arrives in phase 2")`.
- `DemoProviderSet` returns fixture data with `is_demo=True`.
- `ProgramWindow` shows the watermark whenever any `Result` it renders has `is_demo`.

Money is `Decimal` end to end and is formatted only at the view edge. No view computes totals that
the provider did not supply; this keeps the "no arithmetic in prose / no invented numbers" rule
enforceable in phase 2.

---

## 9. States, errors and honesty

`StateView` renders exactly one of:

| State | Look |
|---|---|
| loading | hourglass cursor over the region, marquee block bar, "Reading…" |
| ok | content |
| empty | Cloudy + "Nothing here: <context>" |
| not_connected | Cloudy + "<Program> data arrives in phase 2" (phase 1 default) |
| error | classic ✕ icon dialog panel with the message, *Retry*, and *Details* (traceback hidden by default) |
| partial / denied overlays | a yellow ⚠ banner on top of content: "2 of 3 regions covered · 14 attributes denied", click for detail |

Rules:
- A failure is never rendered as an absence.
- Demo data is never rendered without the watermark.
- Numbers come only from a `Result`.

---

## 10. Accessibility and keyboard

- Full keyboard parity: Alt mnemonics on every menu, logical Tab order, visible dotted focus rectangle, Esc closes dialogs, Enter activates the default button.
- Global shortcuts: Ctrl+K palette · Ctrl+Esc Start · Alt+\` switcher · Ctrl+W close window · Ctrl+Alt+arrows snap · F5 refresh focused window · Ctrl+E export · Ctrl+Q Shut Down… · F1 help.
- Font scale 100–150%, AA contrast in both themes, *Reduce motion* (disables splash animation, CRT effect and saver transitions; savers still run), and an option to disable every sound.

---

## 11. Settings and storage

- `QSettings` (native format per OS). Stored there: theme, font scale, reduce motion, sounds (volume, per-event, mute), screensaver (choice, timeout), skip splash, desktop icon positions, window geometry per program, account nickname and colour, active account, Mac native menu bar.
- App directory: `~/Library/Application Support/AWS95` or `%APPDATA%\AWS95`, mode 0700. It holds logs and exports in phase 1, and engine databases in phase 2.
- Logs: rotating, never contain credentials, never contain resource identifiers at INFO level.

---

## 12. Testing

| Layer | Tests |
|---|---|
| kit | token contrast (AA) for all pairs; FilterSyntax parser table tests; DataGrid sort/filter at 100k rows within budget; StateView transitions |
| shell | registry → Start menu, desktop, palette and help consistency; snapping geometry math; geometry persistence round-trip; **screensaver: input reveals the dialog and does NOT dismiss; Resume click dismisses; random pick per idle session; never starts over a modal**; sound mute and per-event toggles; theme live switch restyles open windows |
| programs | each window opens in Empty mode → not_connected state; in Demo mode → content plus watermark plus `[DEMO]` title |
| contracts | every demo fixture validates against the dataclasses; Decimal for money |
| visual | screenshot tests per program × {Light, Dark} in demo mode (pytest-qt grab plus a perceptual diff with tolerance) |
| perf | startup benchmark (splash, desktop interactive); GUI-stall watchdog in CI debug run |

Tooling: `pytest`, `pytest-qt`, run headless on macOS and Windows (`QT_QPA_PLATFORM=offscreen` where supported).

---

## 13. Packaging and running

- `pyproject.toml` managed with `uv`.
  - Runtime dependencies: `PySide6`, `pyqtgraph`, `numpy` (pyqtgraph needs it; sound generation also uses it in tools), `boto3` (kept for phase 2; unused in phase 1).
  - Dev dependencies: `pytest`, `pytest-qt`, `nuitka`.
- Run: `uv run python -m aws95` or `./run.sh`. Demo: `uv run python -m aws95 --demo`.
- `tools/build_nuitka.py`: macOS `.app` (icns), Windows `.exe` (ico), standalone, onefile optional. App icon regenerated by `tools/make_icons.py`.
- Version is single-sourced in `aws95/__init__.py` and shown in Help → About (classic About dialog with the logo).

---

## 14. Security

- Strictly read-only: no AWS calls at all in phase 1. In phase 2, providers use read-only APIs, documented with a minimal IAM policy.
- No credential storage. Profiles are read through the standard boto3 chain (phase 2).
- Demo mode requires an explicit flag, is always watermarked, and cannot be enabled from settings.
- No telemetry and no network calls in phase 1.
- `.gitignore` excludes databases, reports, `.env*`, `.superpowers/`, `.playwright-mcp/`, `.claude/settings.local.json` and local cost notes.

---

## 15. Milestones (each gets its own implementation plan and branch)

| # | Branch | Delivers | Done when |
|---|---|---|---|
| M1 | `feature/ui-revamp-foundation` | `pyproject`, package skeleton, settings, TaskRunner, theme tokens, `Win95Style`, widget kit (bevels, buttons, DataGrid + FilterSyntax, StateView, Inspector, Watermark, charts, dialogs), contracts, Empty and Demo provider skeletons, kit gallery window for visual QA | kit gallery renders Light and Dark; kit tests green |
| M2 | `feature/ui-revamp-shell` | desktop, ProgramWindow, snapping, Start menu, taskbar, tray, registry, palette, switcher, splash, shutdown, sounds + `make_sounds.py`, screensaver + 5 savers, Display Properties, mascot, Recycle Bin | can boot, open placeholder programs, snap, switch, idle into a saver and Resume |
| M3 | `feature/ui-revamp-programs` | all 9 program windows + demo fixtures + state handling | every program navigable in Empty and Demo modes; screenshot tests green |
| M4 | `feature/ui-revamp-polish` | perf pass against budgets, accessibility pass, Nuitka builds for macOS and Windows, delete legacy UI files, README/CLAUDE.md update | budgets met on packaged builds; legacy removed |

---

## Appendix A: Phase 2 outline (not designed here)

Recorded so the integration thinking isn't lost. It gets its own brainstorm and spec.

- **Engines:** labsnap becomes the single *state* engine (resources, cost, findings, snapshots, diff, trend, fleet). ctwatch becomes the *activity* engine. Both are imported into this repo with history via `git subtree`, under `aws95/engines/`. Their Textual and CLI layers are dropped, and their test suites are kept.
- **Adapters:** `providers/labsnap_*.py` and `providers/ctwatch_*.py` fill the §8 contracts. The old `core/aws_data_collector.py` and `database.py` are retired.
- **Fixes on import:**
  - ctwatch: add an `account` column and multi-region support; parameterise region, bucket and account; bind Athena SQL parameters (closes the f-string injection risk); poll `GetQueryExecution`.
  - labsnap: cache sessions per target (no repeated AssumeRole), refresh assumed credentials, fan regions out in parallel, use adaptive retries, add CE `NextPageToken` handling.
- **Lessons carried over:**
  - fail fast on unreachable regions (`connect_timeout=5`, `read_timeout=10`)
  - wait on futures in submission order
  - "couldn't read" must never be stored as "doesn't exist"
  - restatement-aware cost periods
  - `includeShadowTrails=False`
  - Lambda container images have no `Runtime`
- **Data adoption:** offer to import `~/.labsnap/labsnap.db`, `~/.ctwatch/cache.db` and `~/.labsnap/config.toml` targets into the app directory. Existing ctwatch events get tagged with the STS-discovered account.
- **Auth:** implement the stubbed SSO, assume-role and Organizations providers when available.
- **Bedrock insights** moves behind the `InsightsProvider` contract, sending aggregates only.
