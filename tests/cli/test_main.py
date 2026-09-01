import pytest

from wfm.cli.main import build_parser, main
from wfm.cli.output import table


def test_help_lists_the_planned_subcommands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for command in ("sync", "backfill", "search", "watch", "group"):
        assert command in out


def test_no_arguments_prints_usage_and_returns_nonzero(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_global_flags_exist():
    parser = build_parser()
    args = parser.parse_args(["--json", "--verbose", "search", "mirage"])
    assert args.json is True
    assert args.verbose is True


def test_table_renders_aligned_columns():
    rendered = table(
        [{"slug": "mirage_prime_set", "n": 3}, {"slug": "x", "n": 12}], ["slug", "n"]
    )
    lines = rendered.splitlines()
    assert lines[0].split() == ["slug", "n"]
    assert "mirage_prime_set" in lines[1]
    assert len(set(len(line.rstrip()) > 0 for line in lines)) == 1
