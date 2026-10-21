# Reports and Search (2d, part 1) — Design

**Status:** owner-approved design, 2026-10-18 ("yes, write the spec"). Insights (Bedrock) is 2d part 2, out of scope.
**Builds on:** phase-1 spec `2026-10-02-aws95-ui-revamp-design.md` §6.5 (Ctrl+K palette and `SearchProvider`) and the
Reports row of its program table; first-drop spec `2026-10-07-first-drop-design.md` (store, honesty, money rules);
resumable-snapshots spec `2026-10-16-resumable-snapshots-design.md` (sections).

## 1. Goal

Turn on the last two "arrives in a later update" features that need nothing from AWS:

- **Reports**: the existing Reports window gets live data from the local store.
- **Search**: Ctrl+K also finds resources, findings, principals and billing services from the store, and a hit opens
  the right window filtered to it.

Who reads the reports: **mostly the owner** (working notes). The four existing report types with honest provenance and
a coverage note are enough; no evidence-pack layout.

## 2. Constraints

- **No AWS calls and no cost.** Reports and Search read only the local store `aws95.db` through existing live providers
  (Reports) or one new store-backed provider (Search). `gate.ALLOWED` does not change.
- **Honesty (spec §9):** a gap is stated, never shown as an empty table. No snapshot → the report says so. Demo output
  carries the watermark and `[DEMO]`.
- **Money:** copied exactly from the provider (`Decimal`, `format_value(x, "money")` in the window); the report adds,
  rounds or estimates nothing.
- **Untrusted text:** resource names, principal names and error text are shown plain (`Qt.TextFormat.PlainText`);
  Markdown cells escape `|` and newlines; CSV goes through the existing formula defang.
- **Scope:** only accounts on the roster; the window's ScopeBar (account × region) narrows Reports as it does today.
- **Dark and 110–150 % fonts are Important:** palette goldens in Light, Dark and Dark-150 %.

## 3. Reports (live)

### 3.1 Provider

`LiveReports(resources, billing, findings, activity, snapshots)` implements the existing `ReportsProvider` by composing
the live providers, as `DemoReports` composes the demo ones. It lives in `aws95/providers/live/reports.py` and is wired
in `live_provider_set` in place of `NotConnectedProvider("Reports …")`.

Report types (ids and titles unchanged from demo):

| id | Title | Rows | Source |
|---|---|---|---|
| `inventory` | Resource inventory | account, type, region, name, id, state | `resources.resource_types` + `list_resources` (all pages) |
| `cost` | Cost summary | service, period, amount, unit; then month to date, forecast, last month | `billing.breakdown("service")`, `billing.summary` |
| `findings` | Open findings | severity, rule, account, region, resource, age_days | `findings.list_findings("open")` (all pages) |
| `activity` | Activity watchlist | time, risk, rule, principal, action, account, region | `activity.events(ActivityQuery(risk_min=1))` (all pages) |

Shared table rendering (`_table`, `_md_cell`) moves from `providers/demo/reports.py` to a Qt-free helper both providers
import (`aws95/providers/report_tables.py`), so demo and live render identically.

### 3.2 Coverage note

Under the title, each live report has a **Coverage** list built from the sources' own results, one line each:

- per account, from `snapshots.stages`: "dev — complete (32/32 sections)" or "prod — 29/31 sections · 2 to resume";
  an account with no snapshot: "ops — no snapshot yet";
- the source result's status and message when it is not OK (e.g. "Billing: Not collected — Billing is off for dev.");
- for `cost`, the line "Figures are copied from AWS Cost Explorer as stored; nothing is added or rounded."

The window's existing provenance block (`with_provenance`) still goes above it.

### 3.3 Result status

- Every source OK → `Status.OK`.
- Some source PARTIAL/STALE, or a stage not complete → `Status.PARTIAL`, message "Some data is missing — see Coverage."
- No snapshot for any scoped account (inventory, findings, cost) → `Status.NOT_CONNECTED`, "No snapshots yet. Take
  Snapshot to make the first one."; activity with no events loaded → `Status.NOT_CONNECTED`, "No activity loaded yet.
  Update activity (F5) in Activity Log first."
- A source ERROR → `Status.ERROR` with that source's message. Never an empty report.

## 4. Search

### 4.1 Provider

`LiveSearch(store, roster)` implements the existing `SearchProvider.search(scope, text, limit)` in
`aws95/providers/live/search.py`. Rules:

- `text` is stripped; fewer than 2 characters → `Result.ok(())`.
- Matching is case-insensitive substring (SQL `LIKE` with `%`, `_` and `\` escaped, `ESCAPE '\'`).
- Accounts: the roster (∩ `scope.accounts` when set).
- Up to **8 hits per kind**, kinds in this order; `limit` caps the total.

| kind | Matches | Source | `title` | `subtitle` | target / params |
|---|---|---|---|---|---|
| `resource` | name or id | `resource_state` of each account's latest snapshot | name (or id) | "type · region · account" | `resources` {rtype, filter: `rid:<id>`} |
| `finding` | rule or resource id | open findings of each latest snapshot (mutes applied as in Findings) | rule | "resource · account" | `findings` {rule, account, rid} |
| `principal` | principal name | `events` | principal name | "type · N events" | `activity` {principal} |
| `service` | service name | latest billing service breakdown metrics | service | "Billing · latest month" | `billing` {service} |

`SearchHit` (existing contract) carries `kind`, `title`, `subtitle`, `target_program`, and `payload` = the navigation
params. Search never returns money in the subtitle (no figure without its window's context).

Demo: `DemoSearch(resources, findings, activity, billing)` gives the same kinds from the demo fixtures, `is_demo=True`.
Offline (`empty_provider_set`) stays NOT_CONNECTED; the palette then shows no store hits and no error.

### 4.2 Palette

- Programs, windows and commands match instantly, as today.
- When the query has ≥ 2 characters, after **200 ms** of no typing the palette runs `providers.search` on the
  TaskRunner. Each request carries a generation number; a result for an older query is dropped.
- Hits are added below the existing groups as groups **Resources**, **Findings**, **Principals**, **Services**,
  each row: icon (the target program's icon), title, and the subtitle in the shortcut column. Keyboard navigation and
  Enter work across all groups.
- While a search runs, a plain "Searching local data…" row shows at the bottom; a NOT_CONNECTED or ERROR result shows
  nothing extra (the palette is not the place for errors), except demo results, which are marked `[DEMO]` in the group
  title.
- Choosing a hit calls `shell.navigate(target_program, payload)`.

### 4.3 Navigation added to windows

- **Findings** `apply_navigation`: accepts `rid` in addition to `rule` and `account` (filter `rid:<id>`).
- **Activity** `apply_navigation`: `principal` → sets the principal filter (the same as typing it in the Activity
  filter) and shows the events tab.
- **Billing** `apply_navigation`: `service` → shows the By service view filtered to that service.
- **Resources**: unchanged (`rtype` + `filter`).

## 5. Out of scope

Insights; new AWS calls; scheduled or emailed reports; an audit evidence layout; a separate Search window; full-text
search inside resource attributes or raw events.

## 6. Tests

- **Reports:** each type against the seeded store (exact rows, money copied exactly, coverage lines); a store with an
  unfinished account (PARTIAL + coverage line); an empty store (NOT_CONNECTED, never an empty table); a source ERROR;
  demo and live render the same table shape; protocol satisfied; Reports window loads live types.
- **Search:** each kind matches by name and id; `LIKE` wildcards in the query are literal; < 2 characters returns
  nothing; 8-per-kind cap and total `limit`; roster and scope filtering; muted findings excluded; demo hits marked.
- **Palette:** 200 ms debounce (one search for a burst of keystrokes); a stale result is dropped; hits render in their
  groups; Enter on a hit navigates with the right params; offline adds nothing.
- **Navigation:** Findings by rid, Activity by principal, Billing by service each land filtered.
- **Goldens:** the palette with store hits in Light, Dark and Dark-150 %; Reports window live preview in Light and Dark.
- `tests/test_engine_calls.py` and the gate tests unchanged (no new AWS calls).
