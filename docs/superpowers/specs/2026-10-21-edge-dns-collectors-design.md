# Route 53, API Gateway and CloudFront collectors — Design

**Status:** owner-approved design, 2026-10-21 ("yes, write the spec"). Owner choices: collectors plus six rules;
Route 53 at hosted-zone level only (no record sets).
**Builds on:** first-drop spec `2026-10-07-first-drop-design.md` §3 (collectors, gate, store, rules, findings);
resumable-snapshots spec (sections); billing-complete spec (service map, Uncovered spend).

## 1. Goal

Inventory three services that real accounts pay for but the app doesn't collect yet — Amazon Route 53, Amazon API
Gateway and Amazon CloudFront — and flag their most common security and hygiene gaps.

## 2. Constraints

- **Read-only and free:** only list/get calls; none is billed. Every call is a reviewed gate pair; the engine-call test
  pins the set.
- **Privacy:** API Gateway stage variables are never read into a resource (they can hold secrets). CloudFront domain
  names, aliases and origins are not stored (only counts and flags). Route 53 zone names are stored locally like bucket
  names; AI Insights sends only counts and rule names.
- **Honesty:** a denied per-item call marks that row partial and counts a denial; a rule never fires on an attribute
  that wasn't read.
- **Sections:** each collector is planned and resumed like the others (global collectors: one section; API Gateway: one
  per region).
- **Dark and 110–150 % fonts:** the new Resources group and rows appear in the live and demo goldens.

## 3. Collectors

### 3.1 `route53` (scope `global`, client region `us-east-1`)

| Call | Use |
|---|---|
| `list_hosted_zones` (paginated) | `Id` (rid, without the `/hostedzone/` prefix), `Name`, `Config.PrivateZone`, `ResourceRecordSetCount` |
| `get_dnssec(HostedZoneId)` — public zones only | `Status.ServeSignature` → `dnssec` (`"SIGNING"`, `"NOT_SIGNING"`, …) |
| `list_query_logging_configs(HostedZoneId)` (paginated) — public zones only | `query_logging` = at least one config |

Resource `route53.hosted_zone`: attrs `name`, `private`, `record_count`, and for public zones `dnssec`, `query_logging`
(absent when the call was denied → row partial). Metric `route53.zone_count`.

### 3.2 `apigateway` (scope `regional`)

| Call | Use |
|---|---|
| `apigateway.get_rest_apis` (paginated) | REST APIs: `id`, `name`, `endpointConfiguration.types` |
| `apigateway.get_stages(restApiId)` | per stage: `stageName`, `accessLogSettings` (present → logging on), `tracingEnabled`, `webAclArn` (present → WAF) |
| `apigatewayv2.get_apis` (paginated) | HTTP and WebSocket APIs: `ApiId`, `Name`, `ProtocolType` |
| `apigatewayv2.get_stages(ApiId)` (paginated) | per stage: `StageName`, `AccessLogSettings` |

Resources: `apigateway.api` (rid = API id; attrs `name`, `kind` = `rest`/`http`/`websocket`, `endpoint_type` for REST,
`stage_count`) and `apigateway.stage` (rid = `<api id>/<stage name>`; attrs `api_id`, `kind`, `access_logging`,
`tracing` (REST), `waf` (REST)). Stage `variables` / `StageVariables` are never copied. A denied `get_stages` marks the
API row partial and adds no stage rows. Metric `apigateway.api_count` (dims region, kind).

### 3.3 `cloudfront` (scope `global`, client region `us-east-1`)

| Call | Use |
|---|---|
| `list_distributions` (paginated) | `Id` (rid), `Enabled`, `WebACLId` (non-empty → WAF), `ViewerCertificate` (`CloudFrontDefaultCertificate`, `MinimumProtocolVersion`), the viewer protocol policy of the default and every other cache behaviour, `PriceClass`, `Aliases.Quantity` |
| `get_distribution_config(Id)` | `DistributionConfig.Logging.Enabled` → `access_logging` |

Resource `cloudfront.distribution`: attrs `enabled`, `viewer_protocol` (the weakest across behaviours:
`allow-all` < `redirect-to-https` < `https-only`), `custom_certificate` (bool), `min_tls` (the certificate's minimum
protocol version), `waf`, `access_logging` (absent when denied → partial), `price_class`, `alias_count`. Metric
`cloudfront.distribution_count`.

## 4. Rules (38 → 44)

| id | Severity | Fires when |
|---|---|---|
| `cloudfront.http_allowed` | medium | `viewer_protocol == "allow-all"` |
| `cloudfront.old_tls` | high | `custom_certificate` and `min_tls` is a weak policy (param `weak_policies`, default `SSLv3`, `TLSv1`, `TLSv1_2016`, `TLSv1.1_2016`; amended in the final review so `TLSv1.3_2025` is not reported) |
| `cloudfront.no_waf` | low | `enabled` and not `waf` |
| `cloudfront.no_access_logging` | low | `access_logging is False` (not when absent) |
| `apigateway.stage_no_access_logging` | medium | `access_logging is False` |
| `route53.public_zone_no_query_logging` | low | not `private` and `query_logging is False` |

`rules/sources.py` maps each rule to its collector, so "not collected" stays distinct from "fine" in Fleet and
findings, and tuning (Rules…) works unchanged.

## 5. Wiring

- **Gate:** `LABSNAP` gains `route53.list_hosted_zones`, `route53.get_dnssec`, `route53.list_query_logging_configs`,
  `apigateway.get_rest_apis`, `apigateway.get_stages`, `apigatewayv2.get_apis`, `apigatewayv2.get_stages`,
  `cloudfront.list_distributions`, `cloudfront.get_distribution_config` (9 pairs).
- **IAM** (`permissions.py`, README regenerated): `route53:ListHostedZones`, `route53:GetDNSSEC`,
  `route53:ListQueryLoggingConfigs`, `cloudfront:ListDistributions`, `cloudfront:GetDistributionConfig` join the
  read-only statement. API Gateway's reads are authorised by `apigateway:GET` on resource paths, so they get their own
  statement `AWS95ToolsApiGatewayRead`: action `apigateway:GET`, resources `arn:aws:apigateway:*::/restapis`,
  `arn:aws:apigateway:*::/restapis/*`, `arn:aws:apigateway:*::/apis`, `arn:aws:apigateway:*::/apis/*`. The README
  explains it in one sentence.
- **Resources catalog:** `route53.hosted_zone` and `cloudfront.distribution` under a new domain "Edge & DNS";
  `apigateway.api` and `apigateway.stage` under "Network". `GLOBAL_COLLECTORS` gains `route53`, `cloudfront`.
- **Service map:** "Amazon Route 53" → `route53`, "Amazon API Gateway" → `apigateway`, "Amazon CloudFront" →
  `cloudfront`; `SERVICE_RTYPES` maps them to `route53.hosted_zone`, `apigateway.api`, `cloudfront.distribution`, so
  Billing rows open Resources.
- **Demo fixtures** (`tools/make_demo_fixtures.py`, deterministic): two hosted zones, three APIs with stages, two
  distributions, and findings that exercise the new rules.
- **README:** "38 built-in rules" → 44; the Resources row mentions the new services.

## 6. Out of scope

Route 53 record sets, health checks, resolver; API Gateway authorizers, usage plans, keys and custom domains;
CloudFront functions, origin access, Lambda@Edge; WAF rule contents.

## 7. Tests

- Collectors (fake sessions): fields mapped; pagination followed; the `/hostedzone/` prefix stripped; private zones
  skip DNSSEC/logging calls; stage variables never stored (a fake stage with variables, asserted absent from every
  attr); a denied per-item call → partial row and one denial; weakest viewer protocol across behaviours; default
  certificate → `custom_certificate` False.
- Rules: each fires and doesn't fire, including "attribute absent → no finding".
- Gate: `tests/test_engine_calls.py` finds exactly the new pairs; `tests/test_aws_gate.py` counts (LABSNAP 58 → 67,
  ALLOWED 65 → 74). Permissions: read-only statement still only read verbs; the API Gateway statement scoped as above.
- Catalog/service map: new rtypes appear; Billing rows for the three services are covered and navigate.
- Demo: the Resources tree shows the new group; goldens regenerated (Light/Dark/Dark-150 %) and looked at.
