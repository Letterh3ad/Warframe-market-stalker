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
