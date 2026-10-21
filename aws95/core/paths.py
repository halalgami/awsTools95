"""Per-OS application directory.

POSIX policy (the directory holds sensitive logs, exports and databases):
the directory is created with mode 0700; if it already exists and is owned by
the current user it is ALWAYS re-tightened to 0700 (default path or
AWS95_HOME alike); if it is owned by another user we refuse to use it
(PermissionError). A non-directory at the path propagates FileExistsError.
Windows is unchanged (per-user profile ACLs apply).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AWS95"


def app_dir(create: bool = True) -> Path:
    override = os.environ.get("AWS95_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / APP_NAME
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / APP_NAME
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            if os.stat(path).st_uid != os.getuid():
                raise PermissionError(f"refusing to use app directory not owned by current user: {path}")
            os.chmod(path, 0o700)  # mkdir mode is umask-masked and ignored for existing dirs
    return path
