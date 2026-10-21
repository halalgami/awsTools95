# Findings tools — Design

**Status:** owner-approved design, 2026-10-20 ("go ahead"). Sub-project 3 of three that finish the basic features
before AI Insights (1. Billing complete — `311151d`; 2. Activity and UI polish — `f91500a`; 3. this).
**Builds on:** phase-1 spec `2026-10-02-aws95-ui-revamp-design.md` (Findings dialogs: Rules…, Seed finding…,
Backfill…), first-drop spec §3 (store, findings lifecycle, local mutes), resumable-snapshots spec (one snapshot at a
time; `SnapshotController.add_refusal`). Owner choices (2026-10-20): Rules changes are saved locally and re-checked
now; Seed means an earlier first-seen date; Backfill re-runs today's rules on older snapshots.

## 1. Goal

The three Findings dialogs that are forms only today save and act:

- **Rules…** — turn a rule off, override its severity, edit its parameters, reset to shipped.
- **Seed finding…** — record that a finding has existed since an earlier date.
- **Backfill…** — re-run today's rules over stored snapshots since a date.

## 2. Constraints

- **Local only:** everything is stored in `aws95.db`; nothing reaches AWS; `gate.ALLOWED` is unchanged. Clear Local
  Data removes tuning and seeds with everything else.
- **Honesty:** a disabled rule's findings are hidden (Open, Resolved, Accepted, Fleet, Reports, Search), never shown as
  "resolved"; a tuned rule is labelled "tuned" wherever its id is shown in the Findings window and the Rules dialog;
  seeded dates are labelled "(seeded)" (already shown today).
- **Demo** never writes: every write answers NOT_CONNECTED "… isn't available in demo mode." with `is_demo=True`, as
  Accept does.
- **One job at a time:** Backfill and Take/Resume Snapshot never overlap (each refuses with a plain message).
- **Untrusted text:** notes and parameter values are shown as plain text.
- **Dark and 110–150 % fonts are Important:** goldens for the three dialogs in Light, Dark and Dark-150 %.

## 3. Store (schema v3)

Migration v2 → v3 adds:

```
CREATE TABLE rule_overrides(
  rule       TEXT PRIMARY KEY,
  disabled   INTEGER NOT NULL DEFAULT 0,
  severity   TEXT CHECK (severity IS NULL OR severity IN ('critical','high','medium','low')),
  params     TEXT,                -- JSON object of overridden parameters only; NULL = none
  changed_at TEXT NOT NULL);
```

`seeded_findings` (v1) already holds seeds: `(account, rule, rid, first_seen, source)`; `source` stores the user's note
(or `"manual"` when empty). Store methods: `rule_overrides() -> dict`, `set_rule_override(rule, *, disabled,
severity, params, changed_at)`, `clear_rule_override(rule)`, `seed_finding(account, rule, rid, first_seen, note)`,
`unseed_finding(account, rule, rid)`.

## 4. Engine

- `aws95/aws/engines/labsnap/rules/base.py`: `RuleOverrides.from_store(store)` builds the overrides from
  `rule_overrides`; the "test hook only" docstring changes. `load_rules(overrides)` is unchanged.
- `SnapshotService`'s default rules become `load_rules(RuleOverrides.from_store(store))`, so new snapshots honour
  tuning.
- **Re-check** (`recheck(store, snapshot_ids, rules_all, overrides)`): evaluates each given snapshot with the enabled
  rules and passes every known rule id (enabled or not) as `rules_evaluated`, so a disabled rule's stored findings are
  cleared for that snapshot and can't resurface.
- **Hiding:** `open_findings`, `resolved_findings` and the fleet computation drop rules in `overrides.disabled` before
  anything else, so older snapshots' rows for a disabled rule never read as resolved.
- **Validation** (`validate_params(rule_id, params) -> dict`): every key must be a shipped parameter of that rule; each
  value must have the shipped default's JSON type (int → int, float → int or float, bool → bool, str → str,
  list → list of the default's element type). Errors are plain sentences naming the parameter.

## 5. Provider (`FindingsProvider`)

New methods (live; demo answers NOT_CONNECTED as above):

| Method | Does |
|---|---|
| `set_rule(scope, rule_id, *, enabled: bool, severity: str \| None, params: Mapping \| None) -> Result[RecheckOutcome]` | validates, stores, re-checks each scoped account's latest snapshot |
| `reset_rule(scope, rule_id) -> Result[RecheckOutcome]` | clears the override, re-checks |
| `seed(scope, finding: Finding, first_seen: date, note: str = "") -> Result[None]` | stores the seed; refuses a date on or after the finding's current (unseeded) first seen |
| `unseed(scope, finding: Finding) -> Result[None]` | removes it |
| `backfill(scope, since: date, *, progress=None, stop=None, cancel=None) -> Result[RecheckOutcome]` | re-evaluates every stored snapshot of the scoped accounts taken on or after `since`, oldest first |

`RecheckOutcome(snapshots: int, open_findings: int, cancelled: bool = False)`; its message reads
"Re-checked 2 snapshots: 14 open findings." (Backfill: "Backfilled 40 snapshots since 2026-09-01: 14 open findings.";
cancelled: "Backfill cancelled after 12 of 40 snapshots. What finished is kept.").
`RuleInfo` gains `tuned: bool = False`, `default_severity: str = ""`, `default_params: Mapping = {}`;
`enabled` reflects the override.

## 6. Windows

- **Rules…:** the grid gains a **Tuned** column. Selecting a rule fills an editor below it: **Enabled** checkbox,
  **Severity** combo (critical/high/medium/low, with "(shipped: high)" beside it), a parameter table (Name, Value as
  JSON text, Shipped), **Save**, **Reset to shipped**, and a plain error line. Save/Reset run on the TaskRunner; the
  status line reports the outcome; the grid reloads. Note: "Changes are saved on this computer only and re-check your
  latest snapshots. Nothing is written to AWS."
- **Findings window:** the rule column shows "rule (tuned)" for tuned rules; it reloads after any Rules change.
- **Seed finding…:** opened from the selected finding (menu enabled only with a selection): Rule and Target shown,
  current first seen shown, **First seen** date (default 30 days before it), **Note**, Save; **Remove seed** visible
  when the finding is seeded. Errors (date not earlier) show in the dialog.
- **Backfill…:** **Since** date (default 30 days ago), accounts = the window's scope, a line "Re-runs today's rules
  on stored snapshots. Nothing is read from AWS.", **Start**, **Cancel**, progress "12 of 40 snapshots"; refuses while
  a snapshot runs ("A snapshot is running. Try again when it finishes."). Take/Resume Snapshot refuses while a
  Backfill runs ("Backfill is running. Try again when it finishes.") through `add_refusal`.

## 7. Out of scope

Writing rules or tuning to AWS; custom rule code or rule folders (F3-future); per-account tuning; import/export of
tuning; scheduling backfills.

## 8. Tests

- Store: v3 migration; override and seed methods; Clear Local Data removes them.
- Engine: overrides from the store; new snapshots honour them; re-check clears a disabled rule's findings; disabled
  rules hidden from open/resolved/fleet and back when re-enabled; severity override shown; validation accepts the
  shipped types and rejects unknown keys and wrong types with named messages.
- Provider: set/reset/seed/unseed/backfill results and messages; seed date rule; backfill since-date, oldest first,
  cancel keeps finished work; demo refuses every write.
- Windows: Rules editor saves, resets, shows errors, Tuned column; Findings rule label; Seed dialog flows; Backfill
  progress and cancel; Backfill and snapshot refuse each other.
- Goldens: Rules editor, Seed, Backfill in Light, Dark and Dark-150 %.
- `tests/test_engine_calls.py` and gate tests unchanged.
