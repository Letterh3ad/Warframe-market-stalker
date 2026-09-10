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


def test_classify_is_a_mode():
    args = build_parser().parse_args(["news", "classify"])
    assert args.news_command == "classify"
    assert args.limit is None  # falls back to news_classify_batch


def test_classify_takes_a_limit():
    args = build_parser().parse_args(["news", "classify", "--limit", "3"])
    assert args.limit == 3


def test_a_non_numeric_limit_is_a_parse_error():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["news", "classify", "--limit", "lots"])


def test_relink_is_a_mode():
    args = build_parser().parse_args(["news", "relink"])
    assert args.news_command == "relink"
