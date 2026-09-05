import os

from wfm.daemon import control


def test_pid_round_trip(tmp_path):
    path = tmp_path / "wfm.pid"
    assert control.read_pid(path) is None
    control.write_pid(path, 4321)
    assert control.read_pid(path) == 4321
    control.clear_pid(path)
    assert control.read_pid(path) is None


def test_a_corrupt_pid_file_reads_as_absent(tmp_path):
    path = tmp_path / "wfm.pid"
    path.write_text("not a pid", encoding="utf-8")
    assert control.read_pid(path) is None


def test_is_running_recognizes_this_process():
    assert control.is_running(os.getpid()) is True


def test_is_running_is_false_for_an_impossible_pid():
    assert control.is_running(9_999_999) is False


def test_clear_pid_on_a_missing_file_is_not_an_error(tmp_path):
    control.clear_pid(tmp_path / "absent.pid")
