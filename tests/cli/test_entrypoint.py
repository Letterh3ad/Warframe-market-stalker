import subprocess
import sys


def test_python_m_wfm_runs_and_lists_subcommands():
    result = subprocess.run(
        [sys.executable, "-m", "wfm", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    for command in ("sync", "backfill", "search", "watch", "group"):
        assert command in result.stdout
