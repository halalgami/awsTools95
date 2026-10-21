"""Program registry: one manifest per program feeds Start menu, desktop, palette and shortcuts."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Callable, Literal

from aws95.kit.icons import ICON_NAMES

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from aws95.shell.context import ProgramContext

Category = Literal["programs", "accessories", "settings", "system"]
Instancing = Literal["singleton", "multi"]


@dataclass(frozen=True)
class ProgramManifest:
    id: str
    title: str
    icon: str
    category: Category
    desktop_icon: bool
    shortcut: str | None
    instancing: Instancing
    factory: str                     # "package.module:function"
    keywords: tuple[str, ...] = ()
    order: int = 100

    def load_factory(self) -> "Callable[[ProgramContext], QWidget]":
        module_name, _, func_name = self.factory.partition(":")
        if not module_name or not func_name:
            raise ValueError(f"bad factory string {self.factory!r} for {self.id}")
        return getattr(import_module(module_name), func_name)


class Registry:
    def __init__(self, manifests=()) -> None:
        self._by_id: dict[str, ProgramManifest] = {}
        for m in manifests:
            self.register(m)

    def register(self, manifest: ProgramManifest) -> None:
        if manifest.id in self._by_id:
            raise ValueError(f"duplicate program id {manifest.id!r}")
        if manifest.icon not in ICON_NAMES:
            raise ValueError(f"unknown icon {manifest.icon!r} for {manifest.id!r}")
        if manifest.shortcut is not None:
            for other in self._by_id.values():
                if other.shortcut == manifest.shortcut:
                    raise ValueError(
                        f"shortcut {manifest.shortcut!r} for {manifest.id!r} already used by {other.id!r}")
        self._by_id[manifest.id] = manifest

    def get(self, program_id: str) -> ProgramManifest:
        return self._by_id[program_id]

    def all(self) -> tuple[ProgramManifest, ...]:
        return tuple(sorted(self._by_id.values(), key=lambda m: (m.order, m.title)))

    def by_category(self, category: Category) -> tuple[ProgramManifest, ...]:
        return tuple(m for m in self.all() if m.category == category)

    def desktop(self) -> tuple[ProgramManifest, ...]:
        return tuple(m for m in self.all() if m.desktop_icon)

    def with_shortcuts(self) -> tuple[ProgramManifest, ...]:
        return tuple(m for m in self.all() if m.shortcut)
