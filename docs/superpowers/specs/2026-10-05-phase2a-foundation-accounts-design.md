# AWS 95 Tools — Phase 2a: AWS Foundation, Setup Wizard and Live Accounts

**Date:** 2026-10-05 (revision 2, after the hard review)
**Status:** Implemented (2026-10-06) on `feature/phase2a-foundation`. Final review and the packaged-build check are recorded in `docs/superpowers/followups/2026-10-06-phase2a-followups.md` (private; not in the public repo).
**Branch:** `feature/phase2-triage` holds the spec and plan. Implementation goes on `feature/phase2a-foundation`, created from `feature/ui-revamp-spec`.
**Builds on:** phase 1 (`docs/superpowers/specs/2026-10-02-aws95-ui-revamp-design.md`); triage in `docs/superpowers/followups/2026-10-04-phase2-triage.md` (private; not in the public repo).

---

## 1. Intent

AWS 95 Tools becomes the owner's one-stop explorer for **multiple AWS accounts**. Phase 2 wires real data into the windows that phase 1 built. It is split into four sub-projects, and each one gets its own spec, plan and run:

| Part | Scope |
|---|---|
| **2a (this spec)** | AWS foundation: discovery, sessions, read-only gate, probes, error mapping. Plus the first-run setup wizard, a live Accounts window, and contract and load-lifecycle hardening. |
| 2b | labsnap engine: Resources, Findings, Fleet, Snapshots, Billing. |
| 2c | ctwatch engine: Activity, multi-account, with history read from S3 into SQLite and no Athena (§10). |
| 2d | Insights (Bedrock inside the user's own accounts), Reports, Search. |

### What the owner said
- Use **named profiles from the AWS config files only**. No app-managed assume-role targets and no Organizations discovery.
- **Always read the config files**, and make it visible in the UI that this is where the app looks.
- On first launch, run a **Win95-style setup wizard**. It checks that the files exist, tries the profiles, and checks that each profile has the IAM permissions it needs. The UI must say **which policies are needed**.
- "Dead easy for anyone to download it, go through the first launch setup and see their stuff right away."
- Provide a way to **recheck the config** after a new account is added.
- Copy the engine code into aws95 and **reshape it to fit this project**. aws95 owns it from now on. Drifting from the upstream repos is accepted and expected.
- **2c history:** 90 days by default, with other ranges imported on demand. Event scope is ctwatch's, plus the five additions in §10.

### Decisions
| # | Decision | Choice |
|---|---|---|
| D1 | Account access | Named profiles only: access keys, SSO, and `role_arn` + `source_profile` profiles, which boto3 resolves itself. |
| D2 | Permission check | Live read-only probes, one per capability. No policy simulator. |
| D3 | Profiles found | Wizard checklist. Existing roster entries are pre-ticked; new profiles that verify are ticked. **One profile per account.** A rescan offers new profiles and needs confirmation. |
| D4 | Expired SSO | **Sign In…** runs `aws sso login` when the AWS CLI is found, and shows the device URL and code in the app. Otherwise it shows the command with a Copy button. |
| D5 | Engine code | Copied into `aws95/aws/` and adapted. Only that package imports boto3/botocore. |
| D6 | Partial paging | Keep the rows already fetched, and mark the result PARTIAL with a banner. Exports carry the PARTIAL provenance. |
| D7 | Athena (2c) | Dropped. History is read straight from the trail's S3 log files into SQLite (§10). |

### Assumptions
- The owner's lab account (`123456789012` here, reached through an existing profile) is the first real account.
- Users install the AWS CLI themselves if they want SSO sign-in. The app never installs software.
- **Success means:**
  - On a fresh machine with `~/.aws` set up, launching the app runs the wizard.
  - The Accounts window then lists the verified accounts, with an honest per-capability permission checklist.
  - No code path in aws95 can make an AWS API call outside the reviewed allowlist.

---

## 2. Non-negotiable rules (inherited, restated for this work)

- **Read-only, enforced in code (§4.3).** Every AWS API call aws95 makes goes through the gate, and the gate allows only the explicitly reviewed `(service, operation)` pairs.
  - **SDK credential resolution is outside the gate.** botocore calls `sts:AssumeRole`, `sso:GetRoleCredentials` and `sso-oidc:CreateToken` itself to resolve a profile. That is the AWS SDK doing its job, and the spec does not claim otherwise.
- **aws95 never writes to `~/.aws`.** The AWS SDK and CLI may refresh their own SSO token cache (`~/.aws/sso/cache`) when a profile is used or signed in. That is SDK behaviour, not aws95 writing.
- **No secrets in aws95's hands.**
  - aws95 never reads secret values into any contract, log or UI.
  - Discovery reads only the non-secret keys listed in §4.1.
  - API responses that carry secrets are redacted before they become contracts. This is a 2b requirement; §10 lists the known fields.
- **The account ID always comes from `sts:GetCallerIdentity`**, never from a profile name.
- **Honesty:**
  - A failure is never rendered as empty. An unreadable file is an error, not "0 profiles".
  - Every number and status comes from a `Result`.
  - Provider text is displayed with `Qt.TextFormat.PlainText`. Free text passes through `aws95.core.sanitize`.
  - Demo data is never shown without the watermark and `[DEMO]`. **Demo mode never writes to the real settings.**
- **Logging** records only the error code, service and operation. It never records account IDs, ARNs, profile names or AWS error messages.
- **Layer rules** in §4.6 are enforced by `tests/test_layers.py`.
- **Accessibility:** Dark theme and font scales of 110–150 % are **Important**. Every new screen gets Light, Dark and Dark-150 % goldens.

---

## 3. Architecture

```
aws95/
  aws/                    NEW — the only package that imports boto3/botocore (always lazily, inside functions)
    gate.py               ALLOWED (service, operation) pairs; call() / paginate(); BILLED_SERVICES
    errors.py             botocore exception → Status classifier (from labsnap probe.py)
    session.py            per-call Session factory, client config (timeouts/retries)
    discover.py           config-file locations + profile listing (no secrets), source_profile chain resolution
    identity.py           GetCallerIdentity → (account id, arn)
    permissions.py        capability table → probes + policy JSON
    sso.py                locate `aws` CLI, run `aws sso login`, relay the device URL/code
  providers/live/         NEW
    __init__.py           live_provider_set(settings)
    accounts.py           LiveAccountsProvider
    guard.py              @guarded: any exception → Result (never raises)
  contracts/accounts.py   + ConfigSource, CapabilityCheck, RosterEntry; Profile + selected/issue/verify/sign_in_profile
  shell/setup_wizard.py   NEW — SetupWizard (QWizard, classic style), PermissionsDialog, PolicyDialog
  programs/accounts/      extended: Looking-in header, Status column, Rescan, Use, Check Permissions…, Sign In…, Run Setup…
  core/settings.py        + setup flags, roster, known profiles
```

**Data flow:**
- A program or the wizard calls `ctx.providers.accounts.<method>` on the TaskRunner.
- `LiveAccountsProvider` handles it and calls `aws.gate.call()`, which calls boto3.
- The answer comes back as a `Result[...]`, through `ProgramBase.load()` for programs or through the wizard's own loader, which follows the same generation and cancel rules (§6.4).

**Boot cost:** importing `aws95.providers.live` must not import boto3 or botocore. Measured: importing boto3 at boot adds about 125 ms. A test asserts that `botocore` is not in `sys.modules` after the import.

---

## 4. AWS layer (`aws95/aws/`)

### 4.1 Discovery (`discover.py`)

**Paths.**
- Config: `AWS_CONFIG_FILE`, else `~/.aws/config`.
- Credentials: `AWS_SHARED_CREDENTIALS_FILE`, else `~/.aws/credentials`.
- Same resolution as the AWS CLI, with `~` expanded.

**Parsing.**
- The config file goes through `botocore.configloader.load_config(path)["profiles"]` (it reads only that file, with no environment merge). The credentials file goes through `botocore.configloader.raw_config_parse(path)`.
- Parsing matches the CLI, including its quirk: `[profile has space]` is dropped by botocore while `[profile "has space"]` is kept. aws95 shows what botocore can use.
- **A file that exists but is a directory, unreadable (`os.access(path, os.R_OK)` is false) or malformed** raises `DiscoveryError(path)`. The provider turns it into `Result.error` naming the path. A missing file is reported as `exists=False`, not as an error.

**`config_sources() -> tuple[ConfigSource, ...]`** returns `path`, `kind`, `exists` and `profile_count`.

**`list_profiles() -> tuple[Profile, ...]`**:
- Config profiles come first, in file order, then profiles found only in the credentials file.
- **Keys read:** only `region`, `sso_session`, `sso_start_url`, `role_arn`, `source_profile`, `mfa_serial` and `credential_process`. For the last two, only whether the key is present is used; the value is never copied.
- **Kind:** `sso` if `sso_session` or `sso_start_url` is set; `assume_role` if `role_arn` is set; otherwise `profile`.
- **`sign_in_profile`:** the profile itself if its kind is `sso`. Otherwise, follow `source_profile` up to 5 hops, cycle-safe, to the first `sso` profile. `None` if there is none.
- **`verify`:**
  - `never` if this profile or any profile in its source chain has `mfa_serial`. Such a profile gets `issue="Needs MFA, which isn't supported yet"`. botocore would prompt on a TTY or hang.
  - `manual` if the profile or its chain uses `credential_process`, because the helper may prompt or open windows.
  - `auto` otherwise.
- `region` is the profile's own `region`, else `None`.
- **No secret value enters a contract.** Tests assert that the fixture secrets never appear in `repr()` of any result.

### 4.2 Sessions (`session.py`)
- **`session_for(profile: str)`** imports boto3 lazily and returns `boto3.Session(profile_name=profile)`. A new session is built per call; sessions are **never shared across threads**. An empty or whitespace profile name raises `ValueError` and never falls back to the default credential chain.
- **Client config:** `connect_timeout=5`, `read_timeout=20`, `retries={"mode": "standard", "max_attempts": 3}` and `user_agent_extra="aws95-tools"`. It is built lazily and cached.
- **Regions:**
  - **STS identity** always uses `us-east-1`, so an account where the profile's region is an opt-in region that isn't enabled still verifies.
  - **Other regional probes** use the profile's region, else `us-east-1`.
  - **Cost Explorer** always uses `us-east-1`.

### 4.3 Read-only gate (`gate.py`)
`call(session, service, operation, *, region=None, cancel=None, **params)` and `paginate(...)` are the **only** way aws95 calls AWS. Each one:
1. checks `(service, operation)` against **`ALLOWED`**, an explicit `frozenset` of pairs, and raises `WriteBlockedError` before any client is built;
2. calls `raise_if_cancelled(cancel)` before each call and after each page.

**`ALLOWED` in 2a**, which is exactly the probe set from §4.5:

| Service | Operation |
|---|---|
| `sts` | `get_caller_identity` |
| `ec2` | `describe_regions` |
| `cloudtrail` | `lookup_events` |
| `ce` | `get_cost_and_usage` |

Later parts add pairs only by review. Each added pair carries a comment saying why it is read-only and what it returns.

**`BILLED_SERVICES = {"ce"}`:** every Cost Explorer request costs $0.01, whatever the operation. Billed calls must be opt-in in the UI.

**Enforcement (tests):**
- An AST walk over `aws95/` fails if any module other than `aws95/aws/gate.py` calls an attribute named `client`, `resource`, `get_paginator`, `get_waiter` or `create_client`.
- `boto3` may be imported only by `aws95/aws/session.py`.
- `botocore.session` may be imported nowhere.
- A test proves that `call(session, "s3", "put_object")` and `call(session, "ecr", "get_authorization_token")` raise before any client exists.
- A test asserts that `ALLOWED` equals the set of capability probes plus the reviewed extras, which is empty in 2a.

### 4.4 Error classification (`errors.py`)
Adapted from labsnap's `probe.py`. `classify(exc, service, operation) -> Classified(status, code, message)`:

| Exception | Status | User message (plain) |
|---|---|---|
| `ClientError` with code in `AccessDenied`, `AccessDeniedException`, `UnauthorizedOperation`, `AuthorizationError` | DENIED | "Permission denied for <service>:<Operation>." |
| `EndpointConnectionError`, `ConnectTimeoutError`, `ReadTimeoutError`, `ConnectionClosedError` | UNREACHABLE | "Can't reach AWS (<service>). Check your network or VPN." |
| `TokenRetrievalError`, `UnauthorizedSSOTokenError`, `SSOTokenLoadError` | NOT_CONNECTED | "SSO sign-in expired or missing. Use Sign In…" |
| `NoCredentialsError`, `PartialCredentialsError`, `ProfileNotFound` | NOT_CONNECTED | "No usable credentials for this profile." |
| `ClientError` with code `InvalidClientTokenId`, `SignatureDoesNotMatch`, `ExpiredToken`, `ExpiredTokenException` | NOT_CONNECTED | "The keys for this profile are invalid or expired." |
| `CancelledError` | re-raised | — |
| `WriteBlockedError` | ERROR | "Blocked: aws95 only reads from AWS." |
| anything else | ERROR | "AWS error: <code or exception class name>" |

- The message text AWS returns is **never** used or logged, because it can embed ARNs and account IDs.

### 4.5 Capabilities and policy (`permissions.py`)
**One table is the source of truth.** Each `Capability` has:
- `id`, `label` and `feature`;
- `actions`: the IAM actions it adds to the policy;
- `probe`: a `(service, operation, params)` triple, or `None`;
- `probe_action`;
- `available_in`: `"2a"`, `"2b"`, `"2c"` or `"2d"`;
- `billed`, which is true when the probe's service is in `BILLED_SERVICES`.

| id | Label | Probe | Policy actions |
|---|---|---|---|
| identity | Identity | `sts.get_caller_identity` | `sts:GetCallerIdentity` |
| resources | Resources, Findings, Snapshots | `ec2.describe_regions` | labsnap's read-only set (56 actions, listed in the plan) |
| activity | Activity Log | `cloudtrail.lookup_events(MaxResults=1)` | `cloudtrail:LookupEvents`, `cloudtrail:DescribeTrails` |
| billing | Billing | `ce.get_cost_and_usage` (yesterday, DAILY, UnblendedCost) | `ce:GetCostAndUsage`, `ce:GetCostForecast`, `ce:GetDimensionValues` |
| insights | AI Insights | none | none |

**`policy_json()`** emits one combined statement with `Resource: "*"`.
- Allowed IAM verbs are `Describe`, `List`, `Get`, `Lookup` and `View`. A test enforces this.
- No Athena, Glue or S3-object actions. The scoped S3 read for 2c is added in 2c (§10).

**`check(session, profile_region, include_billing, cancel)`** runs the probes one after another for a profile.
- Billing is skipped (NOT_CONNECTED, "Not checked (AWS charges $0.01 per check)") unless `include_billing` is set.
- Capabilities whose feature hasn't shipped yet are probed and labelled honestly, for example "Ready for Activity Log (arrives in a later update)" or "Missing: cloudtrail:LookupEvents (needed by Activity Log, arriving later)". The wizard doesn't count them as "limited features".

**Wizard copy.**
- **Policy advice:** "Recommended: attach the generated policy below. It allows only what AWS 95 Tools reads. If you prefer an AWS-managed policy, ViewOnlyAccess covers most features; SecurityAudit covers the security checks." The wizard doesn't recommend `ReadOnlyAccess`, because it grants data reads (S3 objects, table items, logs) that a regulated account shouldn't hand out casually.
- **Audit trail:** "Checks are read-only calls. A denied check is recorded in your account's CloudTrail as AccessDenied, which your security team may see."

### 4.6 Layer rules (added to `tests/test_layers.py`)
- Only `aws95/aws/**` imports `boto3` or `botocore`, and only lazily. A test asserts that `botocore` is not in `sys.modules` after `import aws95.providers.live`.
- Only `aws95/providers/live/**` imports `aws95.aws`.
- `programs/`, `kit/`, `shell/` and `core/` import neither. The wizard reaches AWS only through the accounts provider.
- Contracts stay Qt-free and boto3-free.

### 4.7 SSO sign-in (`sso.py`)
**`aws_cli_path()`** uses `shutil.which("aws")`. On macOS, a GUI app's PATH is minimal, so it also checks `/usr/local/bin/aws` and `/opt/homebrew/bin/aws`. On Windows it accepts only an `.exe`. A `.cmd`/`.bat` wrapper would re-parse the arguments, so it's treated as "CLI not found", with the manual command shown instead.

**`sign_in(profile, cancel, on_prompt=None, *, timeout_s=300)`:**
- **Name check:** the profile name must match the argv pattern `[A-Za-z0-9_.+=,@:/][A-Za-z0-9_.+=,@:/-]{0,127}`. The name can't start with `-` and contains no whitespace or control characters. Otherwise it returns `bad_name` and runs nothing.
- **Process:** runs `[cli, "sso", "login", "--profile", profile]` as an argv list with no shell. stdin is DEVNULL. stdout and stderr are merged into a pipe, which a reader thread consumes, keeping at most 64 lines of 512 characters each.
  - On Windows it also passes `CREATE_NO_WINDOW`.
- **Device prompt:** the first URL (`https://\S+`) and the first device code (`[A-Z0-9]{4}-[A-Z0-9]{4}`) are passed to `on_prompt(url, code)` once, so the UI can show them. They're never logged. The CLI also opens the browser itself when it can.
- **Ending:** after `timeout_s` it terminates the process; on cancel it terminates the process. It returns `(exit_code, status)`, where status is one of `ok`, `failed`, `no_cli`, `bad_name`, `timeout` or `cancelled`.
- **SSO behind a role:** a profile whose kind is `assume_role` signs in through its `sign_in_profile`.
- **CLI not found:** the UI shows the install link `https://aws.amazon.com/cli/` and the manual command (`aws sso login --profile <sign_in_profile>`) with a Copy button.

---

## 5. Contracts and providers

### 5.1 Contract changes (`contracts/accounts.py`)
```python
VerifyMode = Literal["auto", "manual", "never"]

@dataclass(frozen=True)
class Profile:
    name: str
    kind: ProfileKind
    region: str | None
    verified: AccountRef | None = None   # live: the roster's stored account (not a fresh check)
    selected: bool = False               # in the roster
    issue: str | None = None             # plain reason it can't be used / is missing
    verify: VerifyMode = "auto"
    sign_in_profile: str | None = None   # profile to pass to `aws sso login`

@dataclass(frozen=True)
class ConfigSource:
    path: str
    kind: Literal["config", "credentials"]
    exists: bool
    profile_count: int

@dataclass(frozen=True)
class CapabilityCheck:
    capability: str
    label: str
    status: Status
    missing_actions: tuple[str, ...]
    message: str
    billed: bool
    available: bool                      # False → feature not shipped yet (label says so)

@dataclass(frozen=True)
class RosterEntry:
    profile: str
    account_id: str
```

### 5.2 `AccountsProvider` (`providers/base.py`)
```python
def list_profiles(self, scope=None, *, cancel=None) -> Result[tuple[Profile, ...]]: ...   # NO network calls
def verify_profile(self, scope, profile="", *, cancel=None) -> Result[AccountRef]: ...
def collector_health(self, scope, *, cancel=None) -> Result[tuple[CollectorHealth, ...]]: ...
def config_sources(self, *, cancel=None) -> Result[tuple[ConfigSource, ...]]: ...
def check_permissions(self, scope, profile="", include_billing=False, *, only_billing=False, cancel=None) -> Result[tuple[CapabilityCheck, ...]]: ...
def sign_in(self, profile="", on_prompt=None, *, cancel=None) -> Result[None]: ...
def required_policy(self, *, cancel=None) -> Result[str]: ...
```
- **`list_profiles` makes no network calls.** It parses the files and joins them with a roster snapshot. A roster profile still in the config gets `verified=AccountRef(stored_account_id, nickname=profile, color="#c0c0c0", profile=profile)` and `selected=True`. A roster profile that's gone from the config is returned with `issue="Not in config any more"`.
- **Providers never touch `Settings`,** because QSettings isn't safe across threads. The live provider holds a lock-protected roster snapshot, which the GUI thread pushes through `set_roster()` whenever `accounts/roster` changes.
- **Nicknames and colours** are applied by the UI from Settings, as phase 1 does.
- **An empty profile name** gets `Result.error("No profile selected.")` from every method that takes one.
- **`collector_health`** stays NOT_CONNECTED until 2b.
- **Demo and empty sets** implement every method. Demo returns fixture data marked `is_demo`; empty returns NOT_CONNECTED.

### 5.3 `@guarded` (`providers/live/guard.py`)
- It wraps every live provider method. It catches every exception except `CancelledError`, classifies it through §4.4, and returns the matching `Result` with a plain message and a `Freshness(now, "attempt")`.
- `DiscoveryError` becomes `Result.error(<message naming the path>)`.
- `Result.error` and `Result.not_connected` gain an optional `freshness=None` keyword.

### 5.4 Provider set selection (`app.py`)
| Flags | Provider set |
|---|---|
| `--demo` or `AWS95_DEMO=1` | demo (unchanged) |
| `--offline` or `AWS95_OFFLINE=1` | `empty_provider_set()`, with the message "<Label> is offline (--offline)." |
| default | `live_provider_set(settings)`: Accounts is live; the other eight domains use `NotConnectedProvider` with "<Label> arrives in a later update." |

`ProviderSet` gains `is_live: bool = False`.

---

## 6. UI

### 6.1 Setup wizard (`shell/setup_wizard.py`)
**Widget.**
- A `QWizard` with `ClassicStyle`. The side panel is painted at runtime (a navy gradient plus `cloudy_pixmap`), scaled to the current font scale.
- Buttons: `< &Back`, `&Next >`, `Cancel`, `&Finish`.
- Non-modal (`show()`). Esc cancels.
- Light, Dark and 150 % goldens.

**When it opens.**
- **Automatically:** at boot, after the first paint, when all of these hold:
  - the provider set is live;
  - `setup_completed` is false;
  - `setup_suppress_autostart` is false;
  - the roster is empty.

  That boot skips the tip of the day.
- **On demand:** Start ▸ Settings ▸ **AWS Setup Wizard…** and the Accounts window's **Run Setup…**, both through `ctx.shell.open_setup_wizard()`.
- **Single instance:** a second request raises the window that's already open.

**Demo mode.** The wizard can be opened, shows demo data, and carries the watermark and `[DEMO]` in its title. **Finish writes nothing** and says so: "Demo mode: nothing was saved."

**Cancel** writes only the "Don't show this at startup" choice. That checkbox appears on the Welcome page and on the Choose page, and the two stay in sync.

**Pages:**
1. **Welcome.** "AWS 95 Tools shows your AWS accounts. It only reads. It never changes anything in your accounts. Nothing leaves this computer except requests to AWS." Plus the startup checkbox.
2. **Where we look.** One row per file: `✔ ~/.aws/config — 4 profiles`, `✖ ~/.aws/credentials — not found`, or `✖ ~/.aws/config — can't be read (check its permissions)`.
   - When there are zero profiles, it shows three steps (install the AWS CLI with its link; `aws configure`; `aws configure sso`), each command with a **Copy** button, plus **Look Again**.
   - **Next** is enabled when at least one profile exists. **Finish without accounts** is always available on this page and the next.
3. **Choose accounts.** A checklist grid with columns: ☐ · Profile · Type · Region · Status.
   - **Verification:** on entering the page, profiles with `verify == "auto"` are verified, at most 4 at a time.
     - `manual` profiles show "Click Verify (uses an external credential helper)" with a **Verify** button.
     - `never` profiles show their issue and can't be ticked.
   - **Status text:**
     - `Checking…`
     - `✔ <account>`
     - `⚠ SSO sign-in needed` with a **Sign In…** button, for any profile with a `sign_in_profile` whose check returned NOT_CONNECTED
     - `✖ <message>`
     - `○ Same account as <profile>`
   - **Ticks:**
     - Existing roster profiles are pre-ticked whatever their current check result. They keep their stored account ID until a check returns a different one.
     - A new profile is ticked when it verified and no ticked row already holds that account.
     - **One profile per account:** ticking a row whose account is already ticked unticks the other one and shows a note.
     - A profile that's neither verified now nor in the roster can't be ticked.
   - Unticking an existing roster profile removes it, which is an explicit user action. Leaving the page cancels any probes still running.
   - **Sign In…** opens a small non-modal dialog that shows "Finish signing in in your browser", plus the device URL and code once the CLI prints them, and a **Cancel** button. After a successful sign-in the row is re-verified.
4. **Check permissions.** One group per ticked account, with its capability rows. Rows for features that haven't shipped yet say so.
   - "Also check Billing access (AWS charges $0.01 per check)" is off by default. Ticking it runs **only** the billing probe.
   - Also on the page: the audit-trail note (§4.5), **Show Required Policy…** and **Check Again**. Gaps never block **Next**.
5. **Name your accounts.** A grid: Profile · Account · Nickname · Colour.
   - The default nickname is the existing label, else the profile name.
   - **Next** stays disabled while any nickname is empty or longer than the limit.
6. **Finish.**
   - The summary is built from results, e.g. "2 accounts ready. Permission gaps: Activity Log on 1 account (feature arrives later)."
   - **Open the Accounts window** is checked by default.
   - **Finish** validates the whole payload first: roster rules (§7) and every label. Only then does it write the labels, the roster (once), and `setup_completed=True`.
   - A validation failure is shown on the page, and the wizard stays open.
   - On success, it plays the `refresh_done` sound if that sound is enabled.

**PolicyDialog.**
- Non-modal. Read-only monospace text with **Copy** and **Save As…**; save errors are shown in the dialog.
- Shows the policy advice from §4.5.

**PermissionsDialog.** A standalone, non-modal dialog for one profile and account, opened from the Accounts window through `ctx.shell.check_permissions(profile, account_id)`.
- It reuses the Check permissions page's panel widget.
- It never writes settings.

### 6.2 Rescan (Accounts window)
**Rescan Config** (F5 in Accounts, also in the Accounts menu):
1. Reloads `config_sources` and `list_profiles`.
2. Verifies the roster profiles, plus the profiles not listed in `accounts/known_profiles` (that is, new since the last rescan or wizard run). Only `auto`-verify profiles are checked; at most 4 run in parallel.
3. Records every current profile name in `known_profiles`.

**What the rescan can surface:**
- **New profiles** that verified, plus **roster profiles whose account changed**, open a non-modal "New Profiles Found" dialog. It lists them as "<profile>: now <new> (was <old>)" and they're ticked. **Add Selected** applies the changes; nothing changes without it.
- **Roster profiles missing from the config** show ⊘ and get a **Remove from List** action. Nothing is removed automatically.

### 6.3 Accounts window changes
- **"Looking in" header row:** "Looking in: ~/.aws/config ✔ · ~/.aws/credentials ✖". Plain text, with the home path shown as `~`.
- **Profiles grid** gains two columns:
  - **Use:** ☑/☐. Toggled with Space or the context menu.
  - **Status:**
    - `✔ Verified` (this session)
    - `✔ Verified <relative>` (remembered from an earlier check: "just now", "N min ago", "N h ago", "N days ago"; shown only when the stored account matches the roster's, otherwise it is ignored and forgotten; opening the window still makes no network call)
    - `● In use (not checked yet)`
    - `⚠ Sign-in needed`
    - `✖ <message>`
    - `⊘ Not in config`
    - `○ Not selected`
    - `— <issue>` for `never` profiles
- **Use toggle:** adds a profile only when its account is known (from a check in this session or from the roster). One profile per account: adding a second profile for the same account replaces the first, after a confirmation balloon.
- **Toolbar and Accounts menu:**
  - **Rescan Config:** F5 overrides the shell's F5 → `refresh()` for this window, so `refresh()` itself rescans.
  - **Check Permissions…**
  - **Sign In…:** enabled only for rows showing `⚠`.
  - **Run Setup…**
  - **Remove from List:** enabled only for rows showing `⊘`.
- **Demo mode:** Use, Remove and Add are disabled, with the tooltip "Not available in demo mode".
- **The phase-1 "Coming soon" group is removed.**
- **Status bar:** profile counts and "Looking in ~/.aws".
- **Roster changes** emit `Settings.changed("accounts/roster")`. ScopeBars in open programs reload their account lists from `list_profiles`, which makes no network calls.

### 6.4 Wizard loading rules
The wizard isn't a `ProgramBase`, but it follows the same rules:
- one generation counter and one `CancelScope` per page;
- the scope is cancelled when the page is left or the wizard closes;
- stale results are dropped, and results for deleted widgets are dropped (`isValid`);
- a failure is shown as the row's status, never as an empty grid.

---

## 7. Settings

**New keys:**
- `setup/completed` (bool) and `setup/suppress_autostart` (bool).
- `accounts/roster`: a JSON list of `{profile, account_id}`.
  - At most 200 entries.
  - Each profile name is non-empty, at most 128 characters, and has no control characters. The roster isn't argv; the argv pattern applies only to sign-in.
  - Account IDs are 12 digits.
  - **Profile names are unique, and account IDs are unique** (one profile per account).
  - Invalid stored entries are skipped when reading.
- `accounts/known_profiles`: a JSON list of names, at most 1000.
- `accounts/verified`: a JSON map `{profile: {"account_id": "<12 digits>", "at": "<ISO-8601 UTC>"}}`, the last successful verification per profile, at most 1000 entries. Validated like the roster (name, 12-digit account, aware UTC time); invalid entries are skipped and a corrupt store reads as empty. Written by the wizard on Finish (rows it verified OK) and by Accounts on every successful verify; forgotten on a failed verify and on Remove from List. Never written in demo mode. API: `Settings.last_verified`, `record_verified`, `forget_verified`.

**API:**
- `Settings.validate_roster(entries)` raises `ValueError` with a plain reason.
- `set_roster` validates and then writes exactly once.
- `known_profiles()` and `set_known_profiles(names)`.

The existing per-account labels and regions, keyed by account ID, are reused unchanged, so no migration is needed.

---

## 8. Hardening carried into 2a (triage items)

| Triage | Change |
|---|---|
| #1 | `ProgramBase.load` cancels the previous handle for the same view. A slow first load that has been superseded never renders. |
| #3 | `TaskRunner.shutdown()` sets `closed`, clears unstarted jobs (`QThreadPool.clear()`), cancels running handles, and empties `_handles`. **Once `closed` is set, `submit` returns an already-cancelled handle that never runs or delivers.** It doesn't raise (raising was measured to crash teardown). A deterministic test covers it. |
| #4 | When a view whose last load ended in ERROR, UNREACHABLE or a failure is shown again (window re-show or tab change), it retries once. F5 is unchanged. |
| #5 | §5.3. |
| #6 | `fetch_all_pages`: a failing page after at least one OK page returns PARTIAL with the rows so far and the message "Showing N rows. Page K failed: <message>". The failed page's coverage is merged, and a DENIED page adds 1 to `denials`, so `Coverage.complete` is false. Exports carry the status. |
| #7 | `ActivityQuery.risk_min` is validated to 0–3, matching `ActivityEvent.risk_level`. A test pins that contracts with Mapping fields are unhashable. |
| #13 | Already done in M1. |

Deferred, and recorded in the triage doc:
- #2 (Activity debounce): 2c.
- #8–#12: 2b.
- #14–#15: revisit later.

---

## 9. Testing

- **Hermetic AWS environment (autouse):**
  - every `AWS_*` variable is removed;
  - `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` point at missing temporary paths;
  - `AWS_EC2_METADATA_DISABLED=true`;
  - `AWS95_OFFLINE=1`, so booting the app in tests never goes live.

  Tests that need files write their own.
- **No network (autouse):** `socket.socket.connect` (INET/INET6), `socket.create_connection` and `socket.getaddrinfo` are patched to **record** the attempt and raise. The fixture **fails the test at teardown** if anything was recorded, so a retried and swallowed network error can't pass silently.
- **Fakes:** `botocore.stub.Stubber` on clients built from `boto3.Session(aws_access_key_id=…, aws_secret_access_key=…, region_name=…)` with explicit fake keys. A `FakeSession` hands these clients to the gate.
- **Discovery:**
  - kinds: keys, sso (`sso_session`), legacy sso, role_arn, role_arn with an SSO source, credential_process, mfa_serial (directly and through the chain);
  - credentials-only profiles;
  - quoted names, and the dropped unquoted-space name;
  - 300 profiles;
  - malformed, directory, unreadable (chmod 0; skipped on Windows and as root), missing, both missing;
  - cycle in `source_profile`;
  - the secrets-leak assertion.
- **Gate:**
  - every allowed pair works;
  - representative dangerous pairs are blocked before any client exists: `ecr.get_authorization_token`, `ec2.get_password_data`, `ssm.get_parameter_history`, `dynamodb.get_item`, `logs.get_log_events`, `lambda.get_function`, `s3.get_object`, `sts.assume_role`;
  - the AST bans;
  - `ALLOWED` equals the probe set;
  - cancel between pages;
  - no botocore after importing `aws95.providers.live`.
- **Errors:** one test per §4.4 row, plus "no ARN or account ID in the message".
- **Permissions:**
  - the policy JSON is valid, uses only read verbs, and contains no Athena, Glue or S3-object actions;
  - every probe action is in the policy;
  - billing is skipped unless opted in, and only billing re-runs when the box is ticked;
  - DENIED lists the missing actions.
- **SSO** (a fake CLI script first on PATH, with `/bin` and `/usr/bin` kept):
  - CLI missing;
  - exit 0 and exit 1;
  - timeout;
  - cancel terminates the process;
  - a bad name runs nothing;
  - the device URL and code are relayed once;
  - an assume_role profile signs in through its `sign_in_profile`;
  - a `.cmd` CLI is refused.

  These tests are POSIX-only. Windows is checked on a real device.
- **Providers:** the `@guarded` contract test across every live method; the demo and empty implementations; empty names; `list_profiles` opens no session.
- **Wizard (pytest-qt):**
  - a walk through all six pages;
  - zero profiles; Finish without accounts;
  - an unreadable file;
  - the duplicate and one-per-account rules;
  - existing roster entries kept when they fail verification;
  - `never` and `manual` profiles;
  - sign-in relay;
  - billing-only re-run;
  - nickname validation blocking Next;
  - Finish validation failure keeps the wizard open with nothing written;
  - Finish writes the roster once;
  - Cancel writes only the suppress choice;
  - demo mode writes nothing and shows `[DEMO]`;
  - single instance;
  - closing mid-probe;
  - autostart rules;
  - the Start-menu entry opens the Welcome page (not `False`);
  - the boot hook opens the wizard in live mode, through a test that clears `AWS95_OFFLINE` and stubs the accounts provider.
- **Accounts window:**
  - header, Status and Use columns;
  - Rescan (new profiles, a changed account, a missing profile, Remove, `known_profiles` limiting which profiles are verified);
  - PermissionsDialog routing;
  - Sign In enablement;
  - demo-mode guards;
  - ScopeBar refresh on a roster change;
  - closing mid-rescan.
- **Hardening:** the tests listed in §8.
- **Goldens:** every wizard page, the policy dialog, the permissions dialog and the updated Accounts window, in Light, Dark and Dark-150 %.
- **Suite budget:** ≤ ~45 s.

### Manual real-account checklist (owner)
- [ ] With a fresh `AWS95_HOME`, the wizard appears on first launch, from the packaged build and from source.
- [ ] The lab profile verifies with the right account ID.
- [ ] The permission checklist matches the profile's real policy. Opt into Billing once.
- [ ] Add a profile to `~/.aws/config` and Rescan: the "New Profiles Found" dialog appears.
- [ ] Expired SSO profile: Sign In… shows the code, the browser completes, and the row turns ✔. Also try a role profile with an SSO source.
- [ ] Rename `~/.aws/config` and run chmod 0 on it: the wizard and the header show ✖ with the right reason.
- [ ] Windows: the CLI is found, no console window flashes, and sign-in works.
- [ ] Light, Dark and 150 %: the wizard, both dialogs and Accounts are readable.

---

## 10. Phase 2c design input: CloudTrail history in SQLite, without Athena (recorded now, built in 2c)

### What ctwatch writes today, and why it goes
- **Glue Data Catalog:** `CREATE EXTERNAL TABLE cloudtrail_logs` in the `default` database (metadata only), created by `ctwatch setup-athena`.
- **S3:** every Athena query writes its full result CSV and a `.metadata` file to `s3://example-cloudtrail-bucket/ctwatch-athena/`, with no lifecycle rule. That makes an unmanaged second copy of who-did-what data inside the log bucket.
- **Other defects:**
  - The shipped policy lacks the `s3:PutObject` and `s3:GetBucketLocation` that Athena's result writes need.
  - `query_principal` builds SQL with f-strings (injection).
  - The table is unpartitioned, so every query scans all 6+ years of logs at $5/TB.
  - `wait()` swallows every error for 60 s.
  - `query_principal` is called nowhere outside its tests.

### Replacement: read the trail files directly
1. **Find the trail:** `cloudtrail.describe_trails` returns the bucket, key prefix, KMS key and whether the trail is an organization trail.
2. **List and download:** list `…/AWSLogs/[<org-id>/]<acct>/CloudTrail/<region>/YYYY/MM/DD/` and download each `*.json.gz`. Unzip and parse locally.
3. **Process:** run the same normalisation and rules as the LookupEvents path, then insert into the SQLite `events` table keyed by `event_id`, so overlap is harmless.
4. **Resume:** keep a watermark per (account, region, day), so an import resumes and never downloads a file twice.
5. **Gate:** add the pairs `("s3", "list_objects_v2")` and `("s3", "get_object")`, under a **parameter validator** that allows only the bucket and prefix `describe_trails` reported, and only keys matching the CloudTrail log pattern. Any other bucket or key raises `WriteBlockedError`, with tests.
6. **KMS:** an encrypted trail also needs `kms:Decrypt` on the trail's key. It gets its own wizard capability probe.
7. **Organization trails:** these usually deliver to a bucket in a separate log-archive account, so a member-account profile often can't read it. 2c must detect this (`describe_trails` reports a bucket in another account, or listing is DENIED) and offer to pick the log-archive account's profile from the roster for the S3 import. The 90-day LookupEvents path still works per account.

### Ranges (owner decision)
- **Default:** 90 days through `LookupEvents`.
- **On demand:** Activity ▸ **Import History…** takes any date range and runs it from S3 as a background job. It can be cancelled, shows progress in the status bar, and the Activity window says how far history goes.

### Event scope (owner decision): ctwatch's scope plus five additions
Applied while parsing, from one editable rules table.
- **Base (ctwatch):**
  - every mutating event;
  - human `AssumeRole`, `AssumeRoleWithSAML`, `AssumeRoleWithWebIdentity` and `GetSecretValue`.
- **Additions:**
  1. All root activity, reads included.
  2. Denied attempts (`errorCode` in the AccessDenied family) by any principal, reads included.
  3. More sensitive human reads: `GetParameter`/`GetParameters` with decryption, `kms:Decrypt`, `GetSessionToken`, `GetFederationToken`, `ListAccessKeys`, `GetCredentialReport`.
  4. `ConsoleLogin`, including failures. **To verify against real log files** whether these already arrive as non-read events.
  5. CloudTrail Insights events (separate `CloudTrail-Insight/` prefix), only when the trail has Insights enabled.
- **Which path gets which addition:**
  - **S3 import:** all five, filtered while parsing.
  - **LookupEvents:** additions 1, 3 and 4 as targeted lookups. Addition 2 runs **forward only, from the first refresh**, and the Activity window shows "Denied attempts tracked since <date>". Backfilling 90 days of read events through LookupEvents would take about 20k calls, roughly 3 hours at 2 requests per second.

### Secrets in 2b responses (redaction required)
- `lambda:ListFunctions` returns `Environment.Variables`.
- `cognito-idp:DescribeUserPoolClient` returns `ClientSecret`.
- `ecs:DescribeTaskDefinition` returns container `environment` variables.

The 2b collectors must drop these fields before building contracts, with tests.

### Other later items
- SSO device-code sign-in without the AWS CLI: needs a security review, because the app would handle tokens.
- MFA profiles (`mfa_serial`): support would need an in-app MFA prompt.
- Packaging and signing: release track. Nuitka must include botocore's data files (plan Task 8).

---

## 11. Rulings

- Ruling: the default nickname is the profile name, so there's no account-alias probe — cost if wrong: one extra probe later.
- Ruling: probes run one after another per profile, with at most 4 profiles in parallel — cost if wrong: a slower wizard with 20+ profiles.
- Ruling: the first-launch wizard reopens each launch until it's finished, finished without accounts, or suppressed — cost if wrong: a mildly nagging first run.
- Ruling: `--offline` keeps the all-NOT_CONNECTED set for tests and screenshots, and the default becomes live — cost if wrong: none for users.
- Ruling: the Cost Explorer probe is opt-in, because each request costs $0.01 — cost if wrong: Billing shows "not checked" until the user ticks the box.
- Ruling (review C1): the gate is an explicit `(service, operation)` allowlist, not a name prefix — cost if wrong: each new API needs a reviewed entry, which is the point.
- Ruling (review C2): after shutdown, `submit` returns a cancelled handle instead of raising — cost if wrong: work submitted late is silently dropped, which is correct during teardown.
- Ruling: one profile per account in the roster — cost if wrong: users can't keep two roles for one account. The fix would be a per-account preferred-profile picker.
- Ruling: MFA profiles are listed but not usable in 2a — cost if wrong: users of MFA-only profiles wait for a later part.
- Ruling: STS identity checks always use us-east-1 — cost if wrong: an SCP that blocks us-east-1 STS would show DENIED; switch to the profile's region.
- Ruling: demo mode never writes settings — cost if wrong: none.
- Ruling: recommend the generated policy, with ViewOnlyAccess or SecurityAudit as the fallback, not ReadOnlyAccess — cost if wrong: one more step for users who wanted the managed shortcut.
