"""Run `aws sso login` (spec §4.7). Output is read only to relay the device URL/code; never logged."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from aws95.core.cancel import CancelToken

PROFILE_ARGV = re.compile(r"[A-Za-z0-9_.+=,@:/][A-Za-z0-9_.+=,@:/-]{0,127}")
_EXTRA_CLI_PATHS: tuple[str, ...] = ("/usr/local/bin/aws", "/opt/homebrew/bin/aws")
INSTALL_URL = "https://aws.amazon.com/cli/"
_URL = re.compile(r"https://\S+")
_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")
_MAX_LINES, _MAX_LINE = 64, 512


def aws_cli_path() -> str | None:
    if sys.platform == "win32":
        # Never resolve from the current directory and never prefer aws.cmd (cmd wrappers re-parse arguments).
        dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and d != "." and os.path.isabs(d)]
        cands = [os.path.join(d, "aws.exe") for d in dirs] + list(_EXTRA_CLI_PATHS)
        return next((c for c in cands if Path(c).is_file()), None)
    return shutil.which("aws") or next((p for p in _EXTRA_CLI_PATHS if Path(p).is_file()), None)


def manual_command(profile: str) -> str:
    """The command shown for the user to run themselves; quoted so names with spaces paste correctly."""
    if sys.platform == "win32":
        quoted = f'"{profile}"' if any(c in profile for c in ' &^|<>"') else profile
    else:
        quoted = shlex.quote(profile)
    return f"aws sso login --profile {quoted}"


def _reader(stream, on_prompt: Callable[[str, str], None] | None) -> None:
    url = code = None
    last: tuple[str, str] | None = None
    for count, raw in enumerate(stream):
        if count >= _MAX_LINES:
            break
        line = raw[:_MAX_LINE]
        url = url or (m.group(0) if (m := _URL.search(line)) else None)
        code = code or (m.group(0) if (m := _CODE.search(line)) else None)
        pair = (url or "", code or "")
        # Newer CLIs use an auth-code flow and print no device code: relay the URL alone, and again if a code follows.
        if url and pair != last and on_prompt is not None:
            last = pair
            try:
                on_prompt(*pair)
            except Exception:  # noqa: BLE001 - the UI may be gone (page closed mid-sign-in); keep draining
                pass
    for _ in stream:                      # keep draining so the CLI never blocks on a full pipe
        pass


def sign_in(profile: str, cancel: CancelToken | None, on_prompt: Callable[[str, str], None] | None = None, *,
            timeout_s: int = 300) -> tuple[int | None, str]:
    if not PROFILE_ARGV.fullmatch(profile or ""):
        return None, "bad_name"
    cli = aws_cli_path()
    if cli is None:
        return None, "no_cli"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    proc = subprocess.Popen([cli, "sso", "login", "--profile", profile], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                            creationflags=flags)
    reader = threading.Thread(target=_reader, args=(proc.stdout, on_prompt), daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            code = proc.poll()
            if code is not None:
                reader.join(timeout=1)
                return code, "ok" if code == 0 else "failed"
            if cancel is not None and cancel.cancelled:
                return None, "cancelled"
            if time.monotonic() >= deadline:
                return None, "timeout"
            time.sleep(0.1)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        reader.join(timeout=1)            # EOF follows process exit; then release the pipe
        if not reader.is_alive():
            proc.stdout.close()
