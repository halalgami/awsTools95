"""SoundBank: low-latency playback of the original sound set; every failure is silent."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QUrl

import aws95
from aws95.core.settings import Settings

log = logging.getLogger(__name__)

EVENTS = ("startup", "shutdown", "window_open", "window_close", "minimize", "maximize", "menu_popup",
          "click", "balloon", "error", "question", "refresh_done", "saver_start")
DEFAULT_OFF = frozenset({"click"})
SOUND_DIR = Path(aws95.__file__).resolve().parent / "assets" / "sounds"

EffectFactory = Callable[[Path, QObject], Any]


def _qt_effect(path: Path, parent: QObject) -> Any:
    from PySide6.QtMultimedia import QSoundEffect
    effect = QSoundEffect(parent)
    effect.setSource(QUrl.fromLocalFile(str(path)))
    return effect


class SoundBank(QObject):
    def __init__(self, settings: Settings, effect_factory: EffectFactory | None = None,
                 sound_dir: Path = SOUND_DIR, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._effects: dict[str, Any] = {}
        factory = effect_factory or _qt_effect
        for event in EVENTS:
            path = sound_dir / f"{event}.wav"
            if not path.is_file():
                continue
            try:
                self._effects[event] = factory(path, self)
            except Exception as exc:  # noqa: BLE001 - no audio backend must never crash the shell
                log.info("sound %s unavailable: %s", event, exc)

    def loaded(self) -> tuple[str, ...]:
        return tuple(self._effects)

    def enabled(self, event: str) -> bool:
        if event not in EVENTS:
            raise KeyError(event)
        if self._settings.sound_muted:
            return False
        return self._settings.sound_event_enabled(event, event not in DEFAULT_OFF)

    def _fire(self, event: str) -> bool:
        effect = self._effects.get(event)
        if effect is None:
            return False
        try:
            effect.setVolume(self._settings.sound_volume / 100.0)
            effect.play()
        except Exception as exc:  # noqa: BLE001
            log.info("sound %s failed: %s", event, exc)
            return False
        return True

    def play(self, event: str) -> bool:
        if not self.enabled(event):
            return False
        return self._fire(event)

    def preview(self, event: str) -> bool:
        if event not in EVENTS:
            raise KeyError(event)
        return self._fire(event)
