# AI Insights (2d part 2) — Design

**Status:** owner-approved design, 2026-10-20 ("yes, write the spec"). Owner choices: each account runs its own Bedrock
call with its own profile; the payload is aggregates plus AWS service and rule names; the model is picked per account
from a list; Analyze shows a token estimate and caps the output.
**Builds on:** phase-1 spec `2026-10-02-aws95-ui-revamp-design.md` (AI Insights window: analysis picker, "What will be
sent" preview, results); `InsightsProvider` contract (`preview_payload`, `analyze(scope, kind, payload)` — "sends
exactly the payload the user reviewed"); first-drop spec §2.1 (cost transparency), the gate (`gate.ALLOWED`).

## 1. Goal

The AI Insights window works in live mode: for each account in scope it previews an anonymised aggregate payload,
and on the user's explicit Analyze sends exactly that payload to an Anthropic model on Amazon Bedrock in that same
account, then shows the answer.

## 2. Constraints

- **Data boundary (bank rule):** nothing leaves the user's AWS accounts. The call goes to Bedrock in the same account
  and region as the data; no other service is contacted.
- **Payload minimisation:** aggregates plus AWS service names and rule names only. Never account ids, resource ids or
  names, ARNs, IP addresses, principal names, tags, user agents or free text from AWS. Accounts are labelled
  "Account 1", "Account 2" in the order shown.
- **Exactly what was reviewed:** `analyze` sends the payload object the user saw, unchanged; any scope or kind change
  invalidates it (the window already enforces this).
- **Explicit action only:** a call happens only on Analyze with the review box ticked; never at boot, on refresh or
  on a schedule.
- **Read-only exception, documented:** the project rule "strictly read-only" gains one named exception —
  `bedrock-runtime:Converse` for AI Insights — in `CLAUDE.md`, `README.md` and the IAM policy. It writes nothing.
- **Cost transparency:** before Analyze the window states the number of calls, an approximate input-token count per
  account and the output cap, and "Bedrock charges per token for the model you choose." No dollar figure (prices vary
  by model and region). README "What costs money" gains the row.
- **Untrusted text:** model output is plain text only (`QPlainTextEdit`, `clean_model_text`), never rich text or
  links; it is not executed or parsed for actions.
- **Not stored:** results live in the window until it closes; they are exported only when the user exports.
- **Dark and 110–150 % fonts are Important:** goldens for the model picker and estimate line.

## 3. Gate

`gate.ALLOWED` gains `("bedrock", "ListFoundationModels")` (read, free) and `("bedrock-runtime", "Converse")`
(invocation, billed; added to `gate.BILLED_SERVICES`). Converse parameters are checked by the gate:
`modelId` (string), `messages` (exactly one user message whose single content block is text), `system` (one text
block), `inferenceConfig.maxTokens` ≤ `MAX_OUTPUT_TOKENS` (1500), `inferenceConfig.temperature` = 0.2; any other key
(tools, guardrail config, documents, images) is refused. `tests/test_engine_calls.py` and the gate tests are updated
for exactly these two pairs.

## 4. Payload (`aws95/aws/insights/payload.py`, Qt-free)

`build_payload(kind, per_account: list[AccountAggregates]) -> dict` with:

```
{"analysis": "<kind>",
 "accounts": [{"label": "Account 1",
               "regions_in_scope": 3,
               "resource_counts": {"ec2.instance": 12, ...},
               "open_findings_by_severity": {"critical": 0, "high": 3, "medium": 5, "low": 1},
               "open_findings_by_rule": {"s3.no_lifecycle": 4, ...},
               "cost_currency": "USD",
               "cost_mtd": "412.3415", "cost_last_month": "598.42",          # exact strings or null
               "top_services_mtd": [["Amazon Elastic Compute Cloud - Compute", "142.18"], ...],   # top 10
               "activity_events_30d": 1234, "risky_events_30d": 17,
               "risky_events_by_rule": {"iam-escalation": 3, ...}}]}
```

Built from the live providers (Resources, Findings, Billing, Activity) per account. A source that is unavailable
contributes `null` with a `"notes"` line (e.g. "Billing is off for this account."). `scrub_check(payload)` asserts no
12-digit number, no `arn:` substring, no IPv4/IPv6 literal and no value equal to any roster account id or profile
name; a hit refuses the preview with an error (defence in depth).

## 5. Model choice

- `InsightsProvider.models(scope_account) -> Result[tuple[ModelChoice, ...]]` lists Anthropic models with TEXT
  output from `bedrock:ListFoundationModels` in that account's region (provider name "Anthropic", output modality
  TEXT, inference type ON_DEMAND), sorted newest first by model id.
- Settings key `insights/model/<account_id>` stores the chosen `modelId`; the window shows one model picker per account
  in scope (label: account nickname — shown locally, never sent).
- No model chosen for an account → that account is skipped with the note "Choose a model for <nickname>."

## 6. Analyze

- `analyze(scope, kind, payload)` makes one `Converse` call per account entry in the payload, in order, with the
  profile and region of that account, the chosen model, the fixed system prompt for `kind`, and the user message
  `json.dumps(account_entry, sort_keys=True)` prefixed by one fixed sentence ("Here are aggregate figures for one AWS
  account. …"). Nothing else is added.
- System prompts (one per kind) live in `aws95/aws/insights/prompts.py`: plain instructions to answer in short
  sections with bullet points, from the figures given only, without inventing numbers.
- `InsightReport` gains `accounts: tuple[AccountInsight, ...]` with `label`, `model`, `sections` (parsed from the
  answer's headings; unparsed text becomes one section) and `usage` (input/output tokens reported by Bedrock).
- Errors are classified: AccessDenied/model-not-enabled → "Model access isn't enabled for <model> in this account.
  Request it in the Bedrock console."; Throttling → "Bedrock is busy. Try again in a minute."; other → the classified
  message. One account's failure doesn't stop the others; the result is PARTIAL with each account's line.
- Cancel (window close or the Stop button) stops before the next call.

## 7. Window

- Above the preview: one model picker per account in scope, a **Refresh models** button, and the estimate line:
  "<n> calls · about <t> input tokens each · up to 1,500 output tokens each. Bedrock charges per token for the model you
  choose." Token estimate = `ceil(len(json) / 4)`, labelled "about".
- Analyze is enabled only when the box is ticked, a preview is loaded and at least one account has a model.
- Results: one block per account ("Account 1 — nickname — model"), sections and bullets as plain text; usage line
  ("1,234 input · 456 output tokens, as reported by Bedrock").
- **Export…** (existing File ▸ Export) writes Markdown with provenance: time, kind, models, the exact payload sent and
  the answers.
- Demo unchanged (canned, watermarked).

## 8. Permissions and docs

- Capability "AI Insights" (`available_in="2d"`, now shipped) probes `bedrock:ListFoundationModels` (free); its
  policy JSON adds `bedrock:ListFoundationModels` and `bedrock:InvokeModel` (Converse is authorised by InvokeModel).
- README: IAM block regenerated; "What costs money" row "AI Insights (on Analyze) | Amazon Bedrock model calls | billed
  per token by the model you choose"; a short "What AI Insights sends" section listing the payload fields.
- CLAUDE.md: the read-only paragraph names the one exception.

## 9. Out of scope

Chat or follow-up questions; tools/agents; storing answers; sending raw events, resource lists or any id; other model
providers; a single central Bedrock account; streaming.

## 10. Tests

- Payload: fields from the seeded store; no id/ARN/IP/name/profile leaks (scrub_check on seeded data and on hostile
  inputs: a resource named like an ARN, a rule name containing an account id); account labels; nulls with notes.
- Gate: only the two new pairs; Converse refuses extra keys, maxTokens > 1500, multiple messages, non-text content.
- Provider: models listing filters and sorting; analyze sends exactly the reviewed entry per account (captured
  request equals `json.dumps(entry)` after the fixed sentence); one call per account; skipped account without a model;
  access-denied and throttling messages; cancel between accounts; PARTIAL when one fails.
- Window: estimate line text; Analyze enabled only when allowed; per-account model pickers remembered; results render
  as plain text; export includes the payload.
- Permissions/README/IAM policy regenerated; `tools/make_readme_policy --check` passes.
- Goldens: Insights live with pickers, estimate and a result, Light/Dark/Dark-150 %.
