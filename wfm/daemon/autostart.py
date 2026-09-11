"""The logon launcher's decisions, kept out of the script that runs them.

Windows runs this through a scheduled task at logon, where nothing is watching: a
launcher that guesses wrong either leaves the daemon down for a day or starts a
second one against the same database. Both answers live here, as pure functions, so
they are testable without a scheduler, a reboot or a subprocess.
"""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import urlparse

from wfm.daemon import control


def should_start_daemon(pid_file: Path) -> bool:
    """Start only what was left running.

    The marker says the last deliberate action was `start`; the pid file plus a
    liveness check says one is already up. A crashed daemon has the marker and a
    stale pid, which is exactly the case that should come back.
    """
    if not control.autostart_enabled(pid_file):
        return False
    pid = control.read_pid(pid_file)
    return not (pid is not None and control.is_running(pid))


def is_listening(url: str, timeout_s: float = 1.0) -> bool:
    """Whether something answers on the URL's host and port.

    Ollama's own readiness endpoint would be a better probe, but this runs before
    anything is up and must not depend on an HTTP client or on Ollama's API shape.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def should_start_ollama(classifier: str, url: str) -> bool:
    """Only for the local backend, and only when nothing already answers.

    The classifier is the one dependency the daemon cannot start for itself, and a
    tick that finds it missing marks articles failed (recoverable six hours later via
    the retry pass, but a wasted window either way).
    """
    return classifier == "ollama" and not is_listening(url)
