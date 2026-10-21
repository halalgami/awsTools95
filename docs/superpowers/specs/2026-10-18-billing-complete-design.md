# Billing complete — Design

**Status:** owner-approved design, 2026-10-18 ("yes, write the spec"). First of three sub-projects that finish the basic
features before AI Insights (owner: "first build out the billing and activity logs fully before touching AI"):
1. **Billing complete** (this spec), 2. Activity and UI polish, 3. Findings tools.
**Builds on:** phase-1 spec `2026-10-02-aws95-ui-revamp-design.md` (the Billing row: eight tabs; the Fleet cost row) and
first-drop spec `2026-10-07-first-drop-design.md` §2.1 (cost transparency), §3 (store, money rules). The owner chose to
include the paid tabs (By region, By usage type).

## 1. Goal

Every Billing tab and the Fleet cost row show real data. Today four tabs (By account, By region, By usage type,
Uncovered spend) and Fleet's cost row say "Not in this release".

## 2. Constraints

- **Cost transparency (first-drop §2.1):** By region and By usage type need **two more Cost Explorer requests per
  account per snapshot**. Every place that states the Billing cost changes from "≈ $0.04" to "≈ $0.06 per account":
  `BILLING_COST_NOTE`, the snapshot prompt, the Billing window's Enable… text, the setup wizard, and the README's
  generated "What costs money" and Billing blocks (from `BILLED_CALLS`, never typed by hand). "One more per extra page
  AWS returns" stays in the note.
- **Opt-in unchanged:** the new requests run only inside the `cost` collector, only for accounts with Billing on.
- **Read-only:** no new `(service, operation)` pair; `ce:GetCostAndUsage` is already allowed. The IAM policy text does
  not change.
- **Money:** `Decimal` end to end; each figure is shown as stored; nothing is summed across accounts in a view; display
  through `format_value(x, "money")`.
- **Honesty:** a tab with no data for its accounts says why (not collected in the latest snapshot / Billing off / no
  snapshot), never empty. Mixed months across accounts give the existing error "These Billing snapshots are from
  different months. Take a new snapshot."
- **Dark and 110–150 % fonts are Important:** goldens for the four tabs in Light, Dark and Dark-150 %.

## 3. Collection (`cost` collector)

After the existing MONTHLY-by-service request, two requests for **this month to date** (the same window as the daily
request), each paginated with the existing `_cost_pages` (a repeated token fails loudly):

| Request | GroupBy | Metric stored | dims |
|---|---|---|---|
| region | `{"Type": "DIMENSION", "Key": "REGION"}` | `cost.region.mtd` | `{"region": <key>}` (Cost Explorer's own key, e.g. `us-east-1`, `global`, `NoRegion`) |
| usage type | `{"Type": "DIMENSION", "Key": "USAGE_TYPE"}` | `cost.usage_type.mtd` | `{"usage_type": <key>}` |

Both use `Metrics=["UnblendedCost"]`, `Granularity="MONTHLY"`, the same `LINKED_ACCOUNT` filter, `period` = the month
(`YYYY-MM`), `unit` from the amount. A denied request counts one denial and the collector carries on (the section ends
`partial`). On the first day of a month the month-to-date window is empty and both requests are skipped, as the daily
request already is (so the count can be one or two fewer that day; the note's "≈" covers it).

`BILLED_CALLS` becomes 6. The collector's call-count test and `tests/test_public_docs.py` follow it.

## 4. Provider (`LiveBilling`)

`breakdown(scope, dimension)` answers all four dimensions from each scoped account's latest snapshot that ran `cost`
(the existing `_latest_with(db, accounts, "cost")`):

| dimension | Rows (`CostPoint`) | Source |
|---|---|---|
| `service` | unchanged | `cost.service.monthly` for the current month |
| `account` | one per account: period = month, amount = that account's month-to-date, `dims={"account": id}` | `cost.total.monthly` for the snapshot's month |
| `region` | one per (account, region) | `cost.region.mtd` |
| `usage_type` | one per (account, usage type) | `cost.usage_type.mtd` |

- Rows carry `dims["account"]` so the window shows which account each figure belongs to. Within one account, figures
  are exactly as stored; there is no cross-account total row.
- A snapshot that ran `cost` but has no `cost.region.mtd` / `cost.usage_type.mtd` rows (taken before this change) →
  that account appears in the result message: "Not collected in the latest snapshot for <ids> — take a new snapshot
  with Billing on." If no scoped account has the rows → `NOT_CONNECTED` with that message.
- Ordering: amount descending, then key.

`uncovered_spend(scope)` returns, for the current month, every service with spend from `cost.service.monthly` whose
name is not covered by a collector, one row per (account, service), `dims={"account", "service", "reason"}`:

- `reason = "No collector"` when the service is not in `SERVICE_COLLECTORS`;
- services in `NOT_A_RESOURCE` (tax, support plans, Cost Explorer itself, marketplace fees) are listed with
  `reason = "Not a resource service"` so nothing is silently dropped.

`SERVICE_COLLECTORS` and `NOT_A_RESOURCE` live in a new Qt-free `aws95/contracts/service_map.py`, which also takes over
`SERVICE_RTYPES` from `programs/resources/catalog.py` (that module re-exports it, so Billing → Resources navigation is
unchanged). The map lists the Cost Explorer service names for every shipped collector (EC2 compute and other, S3,
Lambda, RDS, DynamoDB, IAM, KMS, CloudWatch Logs, CloudTrail, ECR, ECS, ELB, Cognito, Secrets Manager, Bedrock for
guardrails, Budgets, Free Tier/Cost Explorer accounting lines). A test pins every shipped collector to at least one
service name.

## 5. Windows

- **Billing:** the four tabs render their rows; By account, By region, By usage type and Uncovered spend gain an
  Account column (By account is one row per account). Uncovered spend shows the reason column; double-click a row → 
  Resources for that service, as By service does (no mapped type → the existing "No resource type is collected for …"
  status message). Notes under the tabs: By region and By usage type — "This month to date, from Cost Explorer.";
  Uncovered — unchanged text plus "Tax, support and similar lines are listed as not a resource service."
- **Fleet:** the cost row reads `breakdown("account")`: each account column shows that account's month to date, or
  "—" with a tooltip giving the reason (Billing off, no snapshot). The "Cost row: Not in this release." note goes.

## 6. Demo

Demo fixtures gain region and usage-type rows for both demo accounts and uncovered lines (including one
"Not a resource service"), regenerated by `tools/make_demo_fixtures.py` (deterministic). `DemoBilling` answers every
dimension; demo stays watermarked.

## 7. Out of scope

Cross-account totals; tag-based breakdowns; amortised or blended cost; history beyond this month for region and usage
type; budgets editing.

## 8. Tests

- Collector: exactly 6 billed requests on a normal day (the existing call-count test with the new constant); the two
  new requests' parameters (GroupBy key, month-to-date window, LINKED_ACCOUNT filter); pagination and a repeated
  token; a denial counts once and the section stays partial; first-of-month skips them.
- Provider: each dimension from the seeded store (exact amounts, per-account rows); an older snapshot without the new
  metrics → message naming the account; no account with rows → NOT_CONNECTED; mixed months → the existing error;
  Uncovered: no-collector vs not-a-resource reasons, covered services excluded.
- Service map: every shipped collector maps to at least one service name; `SERVICE_RTYPES` still reachable from
  `programs/resources/catalog`.
- Cost note: `BILLING_COST_NOTE` says $0.06; README blocks regenerate (`tools/make_readme_policy --check` passes);
  `tests/test_public_docs.py` updated to 6 / $0.06.
- Windows: Billing tabs render with the Account column; Uncovered double-click navigates; Fleet cost row shows each
  account's figure or "—" with a reason.
- Goldens: By account, By region, By usage type, Uncovered spend in Light, Dark and Dark-150 %; Fleet with the cost
  row in Light and Dark.
- `tests/test_engine_calls.py` and the gate tests unchanged.
