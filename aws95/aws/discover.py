"""Find the AWS config files and list profiles (spec §4.1). Reads only non-secret keys; botocore imported lazily."""
from __future__ import annotations

import os
from pathlib import Path

from aws95.contracts.accounts import ConfigSource, Profile

_SAFE_KEYS = ("region", "sso_session", "sso_start_url", "role_arn", "source_profile", "mfa_serial",
              "credential_process")
_MAX_HOPS = 5
MFA_ISSUE = "Needs MFA, which isn't supported yet"


class DiscoveryError(Exception):
    def __init__(self, path: Path):
        super().__init__(f"Could not read AWS config file: {path}")
        self.path = path


def config_paths() -> tuple[Path, Path]:
    cfg = os.environ.get("AWS_CONFIG_FILE") or "~/.aws/config"
    cred = os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or "~/.aws/credentials"
    return Path(os.path.expandvars(cfg)).expanduser(), Path(os.path.expandvars(cred)).expanduser()


def _parse(path: Path, *, config: bool) -> dict[str, dict]:
    if not path.exists():
        return {}
    if path.is_dir() or not os.access(path, os.R_OK):
        raise DiscoveryError(path)               # RawConfigParser.read() silently skips unreadable files
    from aws95.aws.session import ensure_imported
    ensure_imported()
    from botocore import configloader
    from botocore.exceptions import BotoCoreError
    failed = False
    try:
        raw = configloader.load_config(str(path)).get("profiles", {}) if config else configloader.raw_config_parse(str(path))
    except (BotoCoreError, OSError, UnicodeDecodeError, ValueError):
        failed = True       # raised below, outside the except: botocore's error can carry a raw config line (a secret)
    if failed:
        raise DiscoveryError(path)
    return {str(name): {k: section.get(k) for k in _SAFE_KEYS} for name, section in dict(raw).items()}


def config_sources() -> tuple[ConfigSource, ...]:
    cfg, cred = config_paths()
    return (ConfigSource(str(cfg), "config", cfg.exists(), len(_parse(cfg, config=True))),
            ConfigSource(str(cred), "credentials", cred.exists(), len(_parse(cred, config=False))))


def _kind(section: dict) -> str:
    if section.get("sso_session") or section.get("sso_start_url"):
        return "sso"
    if section.get("role_arn"):
        return "assume_role"
    return "profile"


def _chain(name: str, profiles: dict[str, dict]) -> list[str]:
    seen, out, cur = set(), [], name
    while cur in profiles and cur not in seen and len(out) < _MAX_HOPS:
        seen.add(cur)
        out.append(cur)
        cur = profiles[cur].get("source_profile") or ""
    return out


def _merged() -> dict[str, dict]:
    """One safe-key map the way botocore merges: config sections first, credentials-file keys applied on top."""
    cfg, cred = config_paths()
    merged = {n: dict(sec) for n, sec in _parse(cfg, config=True).items()}
    for name, sec in _parse(cred, config=False).items():
        into = merged.setdefault(name, {k: None for k in _SAFE_KEYS})
        for k, v in sec.items():
            if v:
                into[k] = v
    return merged


def list_profiles() -> tuple[Profile, ...]:
    profiles = _merged()
    out: list[Profile] = []
    for name, section in profiles.items():
        chain = _chain(name, profiles)
        sections = [profiles[n] for n in chain]
        sign_in = next((n for n in chain if _kind(profiles[n]) == "sso"), None)
        if any(s.get("mfa_serial") for s in sections):
            verify, issue = "never", MFA_ISSUE
        elif any(s.get("credential_process") for s in sections):
            verify, issue = "manual", None
        else:
            verify, issue = "auto", None
        out.append(Profile(name, _kind(section), section.get("region") or None,  # type: ignore[arg-type]
                           issue=issue, verify=verify, sign_in_profile=sign_in))  # type: ignore[arg-type]
    return tuple(out)
