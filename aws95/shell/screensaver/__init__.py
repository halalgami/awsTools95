"""Original screensavers (classic concepts, implemented from scratch)."""
from __future__ import annotations

from aws95.shell.screensaver.base import Saver
from aws95.shell.screensaver.bounce import BouncingLogo
from aws95.shell.screensaver.maze import MazeWalk
from aws95.shell.screensaver.mystify import Mystify
from aws95.shell.screensaver.pipes import Pipes
from aws95.shell.screensaver.starfield import Starfield

SAVERS: dict[str, type[Saver]] = {cls.name: cls for cls in (Starfield, Mystify, Pipes, MazeWalk, BouncingLogo)}


def saver_titles() -> dict[str, str]:
    return {name: cls.title for name, cls in SAVERS.items()}
