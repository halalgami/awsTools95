# Upstream: labsnap/labsnap/rules/base.py @ 1b1d437 — adapted for aws95; owned here from now on.
"""The rule registry: labsnap's 38 built-in rules, run as shipped (spec F3). User rule folders (labsnap's
extra_dirs) are deliberately absent: loading code from outside the package is F3-future (spec §9)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from aws95.aws.engines.labsnap.model import Finding

_RULES: dict[str, type] = {}


class DuplicateRule(Exception):
    """Two rule classes registered the same id."""


@dataclass
class RuleOverrides:
    """Local rule tuning (findings-tools spec §4): from the store, or a test's own."""
    disabled: set[str] = field(default_factory=set)
    severity: dict[str, str] = field(default_factory=dict)
    params: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_store(cls, store) -> "RuleOverrides":
        rows = store.rule_overrides()
        return cls({r for r, o in rows.items() if o["disabled"]},
                   {r: o["severity"] for r, o in rows.items() if o["severity"]},
                   {r: o["params"] for r, o in rows.items() if o["params"]})


def shipped_rules() -> dict[str, type]:
    import aws95.aws.engines.labsnap.rules.builtin  # noqa: F401 - importing registers the built-ins
    return dict(_RULES)


_TYPE_WORDS = {bool: "true or false", int: "a whole number", float: "a number", str: "text", list: "a list",
               dict: "an object"}


def _same_type(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, list):
        return isinstance(value, list) and (not default or all(_same_type(default[0], v) for v in value))
    return isinstance(value, type(default))


def validate_params(rule_id: str, params) -> dict:
    """Every key a shipped parameter of the rule; every value the shipped default's JSON type."""
    cls = shipped_rules().get(rule_id)
    if cls is None:
        raise ValueError(f"Unknown rule {rule_id!r}.")
    shipped = dict(getattr(cls, "params", {}) or {})
    out = {}
    for key, value in dict(params).items():
        if key not in shipped:
            raise ValueError(f"{key} isn't a parameter of {rule_id}.")
        default = shipped[key]
        if not _same_type(default, value):
            word = _TYPE_WORDS.get(type(default), type(default).__name__)
            inner = f" of {_TYPE_WORDS.get(type(default[0]), 'values')}" if isinstance(default, list) and default else ""
            raise ValueError(f"{key} must be {word}{inner}.")
        out[key] = value
    return out


@runtime_checkable
class Rule(Protocol):
    id: str
    severity: str
    title: str
    description: str
    params: dict

    def evaluate(self, snap, params: dict) -> Iterable[Finding]: ...


def register_rule(cls):
    existing = _RULES.get(cls.id)
    if existing is not None and existing is not cls:
        raise DuplicateRule(f"rule id {cls.id!r} is already registered by "
                            f"{existing.__module__}.{existing.__qualname__}")
    _RULES[cls.id] = cls
    return cls


def load_rules(overrides: RuleOverrides | None = None) -> list:
    import aws95.aws.engines.labsnap.rules.builtin  # noqa: F401 - importing registers the built-ins
    overrides = overrides or RuleOverrides()
    out = []
    for rule_id, cls in sorted(_RULES.items()):
        if rule_id in overrides.disabled:
            continue
        rule = cls()
        if rule_id in overrides.severity:
            rule.severity = overrides.severity[rule_id]
        merged = dict(getattr(cls, "params", {}) or {})
        merged.update(overrides.params.get(rule_id, {}))
        rule.params = merged
        out.append(rule)
    return out
