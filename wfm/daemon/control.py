from __future__ import annotations

import os
from pathlib import Path


def write_pid(path: Path, pid: int) -> None:
    Path(path).write_text(str(pid), encoding="utf-8")


def read_pid(path: Path) -> int | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def clear_pid(path: Path) -> None:
    Path(path).unlink(missing_ok=True)


# Sits next to the pid file rather than in config or the DB: the logon launcher has
# to answer "was the daemon left running?" before it has a database or an interpreter
# of its own, and a file's existence is the cheapest durable answer there is.
MARKER_NAME = "wfm.autostart"


def marker_path(pid_file: Path) -> Path:
    return Path(pid_file).resolve().with_name(MARKER_NAME)


def set_autostart(pid_file: Path) -> None:
    marker_path(pid_file).write_text("", encoding="utf-8")


def clear_autostart(pid_file: Path) -> None:
    marker_path(pid_file).unlink(missing_ok=True)


def autostart_enabled(pid_file: Path) -> bool:
    return marker_path(pid_file).exists()


def is_running(pid: int) -> bool:
    """True if a process with this pid exists. Windows and POSIX both supported."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError) as exc:
        return isinstance(exc, PermissionError)
    return True
