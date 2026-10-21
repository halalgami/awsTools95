# Activity and UI polish — Design

**Status:** owner-approved design, 2026-10-20 ("yes, write the spec"). Sub-project 2 of three that finish the basic
features before AI Insights (1. Billing complete — merged `311151d`; 2. this; 3. Findings tools).
**Builds on:** phase-1 spec `2026-10-02-aws95-ui-revamp-design.md` (Resources inspector tabs "Attributes, Tags, Raw JSON,
Related activity"; Ctrl+K palette §6.5), first-drop spec (Activity over the store), resumable-snapshots spec (Run tab),
2d part 1 (Ctrl+K store hits).

## 1. Goal

- The Resources inspector's **Related** tab lists the stored Activity events that touched the selected resource.
- Three visible rough edges are fixed: Ctrl+K details don't line up; Snapshots ▸ History cuts off Account and Regions
  at 150 %; the Run tab has no golden while a snapshot runs.

## 2. Constraints

- **No AWS calls:** Related activity reads only events already in the local store (`ActivityProvider.events`).
- **Honesty:** the tab states how events are matched and that the list can be incomplete; with no Activity history for
  the account it says so and points to Activity Log; never an empty grid without a reason.
- **Layers:** `aws95/kit` keeps no provider knowledge: the Inspector exposes a slot for the Related content; the
  Resources program fills it.
- **Untrusted text:** principal names, actions and ARNs render as plain text.
- **Dark and 110–150 % fonts are Important:** goldens in Light, Dark and Dark-150 % for each visible change.

## 3. Related activity

### 3.1 Behaviour

- When a resource is selected, the Resources program loads (secondary load, `status=False`, cancelled and dropped on
  re-selection by the existing generation guard):
  `fetch_all_events(activity, Scope(accounts=(row.account,)), ActivityQuery(arn_contains=row.rid), limit=200)`.
- The tab shows a grid: **Time** (UTC), **Principal**, **Action**, **Account**, **Region**, **Risk**, newest first,
  at most **200** rows. When the cap is hit, the note adds "Showing the newest 200."
- Note above the grid (plain text): "Matched on the resource ID in each event's ARNs. CloudTrail doesn't record a
  resource for every event, so this list can be incomplete."
- No events in the loaded history → empty state "No stored events name this resource." (the note still shows).
- The provider answers NOT_CONNECTED for an account with no history → that message is shown ("No Activity history is
  loaded for …"), with the existing Activity wording.
- A button **Open in Activity Log** calls `navigate("activity", {"arn": rid, "account": account})`.
- Clearing the selection clears the grid and puts the tab back to "Select a resource to see its activity."

### 3.2 Activity navigation

`ActivityProgram.apply_navigation` gains `arn`: sets the Filter tab's **ARN contains** field to the value, the Filter
tab is shown and run (as `principal` does today). An `account` param, when present, narrows the window's ScopeBar to
that account if it is in the roster (the existing ScopeBar API); otherwise it is ignored.

### 3.3 Inspector

`Inspector` gets `set_related(widget: QWidget)` replacing the placeholder StateView in the Related tab, and keeps its
"phase 2" placeholder only when no program sets one (gallery). The Resources program builds the related panel
(note label, grid in a StateView, button) in `aws95/programs/resources/related.py`.

## 4. UI polish

### 4.1 Ctrl+K details column

Palette rows keep title and detail (shortcut or search subtitle) as separate data. A small item delegate in
`aws95/shell/palette.py` paints the title left-aligned and the detail right-aligned in the theme's muted text colour,
eliding the **detail** (never the title) when the row is narrow. The "\t" join goes away; `visible_items()` and
keyboard behaviour are unchanged.

### 4.2 Snapshots ▸ History

The History grid's **Account** and **Regions** columns size to their contents (`Column(..., width=-1)`), so
neither is elided at 150 % (the Run tab already does this).

### 4.3 Run tab while running

A golden of the Run tab while a snapshot runs: one account with a section `running`, its stage line
("prod — 12/42 sections · running: lambda eu-west-1"), Cancel enabled, Resume disabled — in Light, Dark and Dark-150 %.
Built from a fake Snapshots provider in the test (no engine run).

## 5. Out of scope

Matching events by tags, names inside request parameters, or raw event bodies; loading Activity from the inspector;
related findings; changing the Activity window's own tabs.

## 6. Tests

- Related: a selected resource shows its matching events (seeded store) newest first; another account's events are
  excluded; the 200 cap with the "newest 200" note; NOT_CONNECTED shows the provider message; no events → the empty
  message; re-selecting quickly never shows the previous resource's rows; Open in Activity Log navigates with `arn`
  and `account`.
- Activity navigation: `arn` fills ARN contains, shows the Filter tab and runs it; `account` narrows the scope.
- Inspector: `set_related` replaces the placeholder; gallery keeps it.
- Palette: title and detail are separate; the delegate never elides the title; details right-aligned (pixel check on
  the painted row); Enter/selection unchanged.
- History: Account and Regions columns are at least as wide as their longest value at 150 %.
- Goldens: Resources with the Related tab, palette rows, Snapshots History at 150 %, Run tab running — Light, Dark,
  Dark-150 %.
