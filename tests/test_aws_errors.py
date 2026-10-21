import pytest
from botocore.exceptions import (ClientError, ConnectionClosedError, CredentialRetrievalError,
                                 PartialCredentialsError, ProxyConnectionError, SSLError, ConnectTimeoutError, EndpointConnectionError, NoCredentialsError,
                                 ProfileNotFound, ReadTimeoutError, SSOTokenLoadError, TokenRetrievalError,
                                 UnauthorizedSSOTokenError)

from aws95.aws.errors import classify
from aws95.aws.gate import WriteBlockedError
from aws95.contracts.common import Status
from aws95.core.cancel import CancelledError


def _ce(code):
    return ClientError({"Error": {"Code": code, "Message": "arn:aws:iam::123456789012:user/secret-name denied"}},
                       "GetCallerIdentity")


@pytest.mark.parametrize("exc,status", [
    (_ce("AccessDenied"), Status.DENIED), (_ce("AccessDeniedException"), Status.DENIED),
    (_ce("UnauthorizedOperation"), Status.DENIED), (_ce("AuthorizationError"), Status.DENIED),
    (EndpointConnectionError(endpoint_url="https://x"), Status.UNREACHABLE),
    (ConnectTimeoutError(endpoint_url="https://x"), Status.UNREACHABLE),
    (ReadTimeoutError(endpoint_url="https://x"), Status.UNREACHABLE),
    (TokenRetrievalError(provider="sso", error_msg="expired"), Status.NOT_CONNECTED),
    (UnauthorizedSSOTokenError(), Status.NOT_CONNECTED),
    (SSOTokenLoadError(error_msg="none"), Status.NOT_CONNECTED),
    (NoCredentialsError(), Status.NOT_CONNECTED), (ProfileNotFound(profile="x"), Status.NOT_CONNECTED),
    (_ce("InvalidClientTokenId"), Status.NOT_CONNECTED), (_ce("ExpiredToken"), Status.NOT_CONNECTED),
    (_ce("SignatureDoesNotMatch"), Status.NOT_CONNECTED), (_ce("ExpiredTokenException"), Status.NOT_CONNECTED),
    (SSLError(endpoint_url="https://x", error="bad cert"), Status.UNREACHABLE),
    (ProxyConnectionError(proxy_url="http://p"), Status.UNREACHABLE),
    (ConnectionClosedError(endpoint_url="https://x"), Status.UNREACHABLE),
    (CredentialRetrievalError(provider="process", error_msg="boom"), Status.NOT_CONNECTED),
    (PartialCredentialsError(provider="env", cred_var="secret_key"), Status.NOT_CONNECTED),
    (WriteBlockedError("s3.put_object"), Status.ERROR),
    (_ce("ThrottlingException"), Status.ERROR), (ValueError("weird"), Status.ERROR),
])
def test_classify(exc, status):
    c = classify(exc, "sts", "GetCallerIdentity")
    assert c.status is status
    assert "123456789012" not in c.message and "secret-name" not in c.message


def test_classify_reraises_cancel():
    with pytest.raises(CancelledError):
        classify(CancelledError())


def test_denied_message_names_action():
    assert classify(_ce("AccessDenied"), "cloudtrail", "LookupEvents").message == \
        "Permission denied for cloudtrail:LookupEvents."


def test_other_error_shows_code_only():
    assert classify(_ce("ThrottlingException"), "sts", "X").message == "AWS error: ThrottlingException"
