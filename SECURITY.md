# Security policy

## Reporting a vulnerability

Please report security problems **privately**, through GitHub's private vulnerability reporting:
https://github.com/halalgami/awsTools95/security/advisories/new (the repository's Security tab ▸ Report a vulnerability).

Please don't open a public issue, pull request or discussion for a vulnerability.

Include the version (shown in About, F1, or by `--version`), your operating system, the steps to reproduce, and the impact you expect. Never include credentials, real account IDs or data from your AWS accounts; use placeholders such as `123456789012`. The maintainer replies in the advisory thread.

## Supported versions

Only the latest release gets security fixes.

## What matters most

- Any way the app could write to AWS, call an operation outside the reviewed list in `aws95/aws/gate.py`, or send AI Insights anything beyond the reviewed, anonymised aggregates.
- Credentials, tokens or secret values reaching the local database, logs, exports or the screen.
- Any network destination other than AWS.
- Running code from outside the app's own package.
