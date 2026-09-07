from wfm.cli.main import build_parser


def test_news_is_a_registered_subcommand():
    args = build_parser().parse_args(["news", "status"])
    assert args.command == "news"
    assert args.news_command == "status"


def test_ingest_is_the_other_mode():
    args = build_parser().parse_args(["news", "ingest"])
    assert args.news_command == "ingest"
    assert args.force is False


def test_ingest_takes_a_force_flag():
    args = build_parser().parse_args(["news", "ingest", "--force"])
    assert args.force is True


def test_news_without_a_mode_is_a_parse_error(capsys):
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["news"])
