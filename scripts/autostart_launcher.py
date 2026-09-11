"""Started by a Windows logon task. Brings back only what was left running.

Deliberately tiny and silent: nothing watches it, so it makes one decision
(wfm/daemon/autostart.py), spawns at most two detached processes and exits. It never
stops anything, and it never starts a second daemon against the same database.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from wfm.config import Config  # noqa: E402
from wfm.daemon import autostart  # noqa: E402

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: the launcher exits immediately and the
# task ends with it, so anything still tied to its console would die too.
DETACHED = 0x00000008 | 0x00000200

OLLAMA_READY_TIMEOUT_S = 30.0
OLLAMA_POLL_INTERVAL_S = 0.5


def _spawn(command: list[str]) -> None:
    subprocess.Popen(
        command,
        cwd=REPO,
        creationflags=DETACHED,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_for_ollama(url: str) -> bool:
    deadline = time.monotonic() + OLLAMA_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if autostart.is_listening(url):
            return True
        time.sleep(OLLAMA_POLL_INTERVAL_S)
    return False


def main() -> int:
    config = Config.load(REPO / "wfm.toml")
    pid_file = REPO / config.pid_file
    if not autostart.should_start_daemon(pid_file):
        return 0

    if autostart.should_start_ollama(config.news_classifier, config.news_ollama_url):
        ollama = shutil.which("ollama")
        if ollama is not None:
            _spawn([ollama, "serve"])
            # Started before the daemon and waited for, because the first news tick
            # fires on the daemon's first loop iteration: an Ollama that is still
            # loading then costs that tick's articles a failure (recoverable by the
            # six-hourly retry, but a wasted window).
            _wait_for_ollama(config.news_ollama_url)

    _spawn([sys.executable, "-m", "wfm", "daemon", "start"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
