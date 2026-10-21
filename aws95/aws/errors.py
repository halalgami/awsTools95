"""botocore exception → Status + plain message (spec §4.4; adapted from labsnap probe.py).
AWS-provided message text is never used: it can embed ARNs and account ids."""
from __future__ import annotations

from typing import NamedTuple

from aws95.aws.gate import WriteBlockedError
from aws95.contracts.common import Status
from aws95.core.cancel import CancelledError

DENIED_CODES = frozenset({"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "AuthorizationError"})
BAD_KEY_CODES = frozenset({"InvalidClientTokenId", "SignatureDoesNotMatch", "ExpiredToken", "ExpiredTokenException"})


class Classified(NamedTuple):
    status: Status
    code: str
    message: str


def classify(exc: BaseException, service: str = "", operation: str = "") -> Classified:
    if isinstance(exc, CancelledError):
        raise exc
    if isinstance(exc, WriteBlockedError):
        return Classified(Status.ERROR, "WriteBlocked", "Blocked: aws95 only reads from AWS.")
    from aws95.aws.session import ensure_imported
    ensure_imported()
    from botocore import exceptions as be
    action = f"{service}:{operation}" if service and operation else "this request"
    if isinstance(exc, be.ClientError):
        code = str(exc.response.get("Error", {}).get("Code", "ClientError"))
        if code in DENIED_CODES:
            return Classified(Status.DENIED, code, f"Permission denied for {action}.")
        if code in BAD_KEY_CODES:
            return Classified(Status.NOT_CONNECTED, code, "The keys for this profile are invalid or expired.")
        return Classified(Status.ERROR, code, f"AWS error: {code}")
    if isinstance(exc, (be.ConnectionError, be.ReadTimeoutError, be.ConnectionClosedError)):   # ConnectionError: SSL, proxy, endpoint, timeouts
        return Classified(Status.UNREACHABLE, type(exc).__name__,
                          f"Can't reach AWS ({service or 'AWS'}). Check your network or VPN.")
    if isinstance(exc, (be.TokenRetrievalError, be.UnauthorizedSSOTokenError, be.SSOTokenLoadError)):
        return Classified(Status.NOT_CONNECTED, type(exc).__name__, "SSO sign-in expired or missing. Use Sign In…")
    if isinstance(exc, be.CredentialRetrievalError):
        return Classified(Status.NOT_CONNECTED, type(exc).__name__, "The credential helper for this profile failed.")
    if isinstance(exc, (be.NoCredentialsError, be.PartialCredentialsError, be.ProfileNotFound)):
        return Classified(Status.NOT_CONNECTED, type(exc).__name__, "No usable credentials for this profile.")
    return Classified(Status.ERROR, type(exc).__name__, f"AWS error: {type(exc).__name__}")
