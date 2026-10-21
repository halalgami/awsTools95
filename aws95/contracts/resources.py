from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResourceType:
    rtype: str          # "ec2.instance"
    domain: str         # "Compute"
    label: str          # "EC2 Instances"
    count: int | None = None


@dataclass(frozen=True)
class ResourceRow:
    account: str
    rtype: str
    region: str          # "global" for account-scoped resources
    rid: str
    name: str
    state: str | None
    attrs: Mapping[str, Any] = field(default_factory=dict)
    tags: Mapping[str, str] = field(default_factory=dict)
    partial: bool = False
