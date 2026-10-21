# Upstream: labsnap/labsnap/collect/collectors/cognito.py @ 1b1d437 — adapted for aws95; owned here from now on.
from aws95.aws import gate
from decimal import Decimal
from aws95.aws.engines.labsnap.collect import redact
from aws95.aws.engines.labsnap.collect.base import register
from aws95.aws.engines.labsnap.collect.probe import probe
from aws95.aws.engines.labsnap.model import CollectorResult, Metric, Resource


@register
class CognitoCollector:
    name = "cognito"
    scope = "regional"
    timeout_s = 300          # fans out per pool and per client
    # A live count of registered users, not a configuration setting.
    volatile_keys = frozenset({"estimated_users"})

    def collect(self, session, region, cancel=None):
        idp = gate.bind(session, "cognito-idp", region, cancel=cancel)
        resources, denials = [], 0
        pool_count = 0

        for page in idp.pages("list_user_pools", MaxResults=60):
            for entry in page["UserPools"]:
                pool_id = entry["Id"]
                pool_count += 1
                # The pool ID is the rid, not the name. Names are editable;
                # a rename would otherwise read as a deletion plus a creation.
                attrs, partial = {}, False

                detail, unreadable = probe(idp.describe_user_pool,
                                           UserPoolId=pool_id)
                denials += unreadable
                partial = partial or unreadable
                if not unreadable:
                    pool = (detail or {}).get("UserPool", {})
                    policy = (pool.get("Policies") or {}).get("PasswordPolicy", {})
                    attrs.update({
                        "mfa_configuration": pool.get("MfaConfiguration"),
                        "password_min_length": policy.get("MinimumLength"),
                        "password_requires_uppercase":
                            bool(policy.get("RequireUppercase")),
                        "password_requires_lowercase":
                            bool(policy.get("RequireLowercase")),
                        "password_requires_numbers":
                            bool(policy.get("RequireNumbers")),
                        "password_requires_symbols":
                            bool(policy.get("RequireSymbols")),
                        "temp_password_validity_days":
                            policy.get("TemporaryPasswordValidityDays"),
                        # Self-registration is on unless admins are the only
                        # ones who may create users.
                        "self_signup_allowed": not bool(
                            (pool.get("AdminCreateUserConfig") or {})
                            .get("AllowAdminCreateUserOnly")),
                        "advanced_security_mode":
                            (pool.get("UserPoolAddOns") or {})
                            .get("AdvancedSecurityMode"),
                        "estimated_users": pool.get("EstimatedNumberOfUsers"),
                    })

                client_pages, unreadable_cl = probe(
                    lambda pid=pool_id: list(
                        idp.pages("list_user_pool_clients", UserPoolId=pid, MaxResults=60)))
                denials += unreadable_cl
                partial = partial or unreadable_cl
                if not unreadable_cl:
                    clients = [c for p in (client_pages or [])
                               for c in p.get("UserPoolClients", [])]
                    # Zero clients and "could not list clients" are different
                    # facts; only the first gets a number.
                    attrs["client_count"] = len(clients)
                    for c in clients:
                        cdetail, unreadable_c = probe(
                            idp.describe_user_pool_client,
                            UserPoolId=pool_id, ClientId=c["ClientId"])
                        redact.user_pool_client(cdetail)       # spec §3.2: ClientSecret never reaches anything else
                        denials += unreadable_c
                        cattrs = {"pool_id": pool_id}
                        if not unreadable_c:
                            uc = (cdetail or {}).get("UserPoolClient", {})
                            cattrs.update({
                                "explicit_auth_flows":
                                    list(uc.get("ExplicitAuthFlows") or []),
                                # Counted, never stored: callback URLs carry
                                # internal hostnames and application paths.
                                "callback_url_count":
                                    len(uc.get("CallbackURLs") or []),
                                "has_client_secret":
                                    bool(uc.get("GenerateSecret")),
                                "prevent_user_existence_errors":
                                    uc.get("PreventUserExistenceErrors"),
                            })
                        resources.append(Resource(
                            "cognito.user_pool_client", region,
                            c["ClientId"], cattrs, unreadable_c))

                resources.append(Resource("cognito.user_pool", region,
                                          pool_id, attrs, partial))

        metrics = [Metric("cognito.user_pool_count", Decimal(pool_count),
                          dims={"region": region})]
        return CollectorResult(resources, metrics, [region], denials)
