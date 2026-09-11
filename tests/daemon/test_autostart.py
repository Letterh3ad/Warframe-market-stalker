"""What the logon launcher decides, without a scheduler or a reboot."""

from pathlib import Path

import pytest

from wfm.daemon import autostart, control


@pytest.fixture
def pid_file(tmp_path) -> Path:
    return tmp_path / "wfm.pid"


def test_a_daemon_left_running_comes_back(pid_file):
    control.set_autostart(pid_file)
    assert autostart.should_start_daemon(pid_file) is True


def test_a_daemon_deliberately_stopped_stays_down(pid_file):
    control.set_autostart(pid_file)
    control.clear_autostart(pid_file)
    assert autostart.should_start_daemon(pid_file) is False


def test_a_crashed_daemon_comes_back_despite_its_orphaned_pid_file(pid_file):
    control.set_autostart(pid_file)
    # A pid no process can own: the crash left the file behind.
    control.write_pid(pid_file, 2**31 - 1)
    assert autostart.should_start_daemon(pid_file) is True


def test_a_daemon_already_running_is_not_started_twice(pid_file, monkeypatch):
    control.set_autostart(pid_file)
    control.write_pid(pid_file, 4242)
    monkeypatch.setattr(control, "is_running", lambda pid: True)
    assert autostart.should_start_daemon(pid_file) is False


def test_the_marker_sits_beside_the_pid_file_whatever_it_is_called(tmp_path):
    assert control.marker_path(tmp_path / "custom.pid").name == "wfm.autostart"
    assert control.marker_path(tmp_path / "custom.pid").parent == tmp_path.resolve()


def test_ollama_is_started_only_for_the_ollama_backend(monkeypatch):
    monkeypatch.setattr(autostart, "is_listening", lambda url, timeout_s=1.0: False)
    assert autostart.should_start_ollama("ollama", "http://localhost:11434") is True
    assert autostart.should_start_ollama("none", "http://localhost:11434") is False
    assert autostart.should_start_ollama("claude", "http://localhost:11434") is False


def test_a_running_ollama_is_left_alone(monkeypatch):
    monkeypatch.setattr(autostart, "is_listening", lambda url, timeout_s=1.0: True)
    assert autostart.should_start_ollama("ollama", "http://localhost:11434") is False


def test_is_listening_says_no_for_a_port_nothing_holds():
    # Port 1 is privileged and unused; a refused connection is the "nothing here"
    # answer this has to get right, since a false positive leaves Ollama down.
    assert autostart.is_listening("http://127.0.0.1:1", timeout_s=0.2) is False
