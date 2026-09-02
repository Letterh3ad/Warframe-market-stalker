import io
from datetime import datetime, timezone

from wfm.alerts.terminal import TerminalSink
from wfm.models import Direction, Horizon, Signal

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _signal(signal_id: int = 1) -> Signal:
    return Signal(
        id=signal_id, slug="x", rank=0, analyzer="revert", ts=NOW, direction=Direction.BUY,
        magnitude=2.0, confidence=0.7, horizon=Horizon.DAILY, evidence={"robust_z": -2.0},
    )


async def test_writes_every_signal_and_reports_them_delivered():
    stream = io.StringIO()
    result = await TerminalSink(stream=stream).deliver([_signal(1), _signal(2)])
    assert result.sink == "terminal"
    assert result.delivered == [1, 2]
    assert result.failed == []
    assert stream.getvalue().count("revert") == 2


async def test_uses_the_display_name_when_one_is_known():
    stream = io.StringIO()
    await TerminalSink(stream=stream, names={"x": "Ex Item"}).deliver([_signal()])
    assert "Ex Item" in stream.getvalue()


async def test_delivering_nothing_writes_nothing():
    stream = io.StringIO()
    result = await TerminalSink(stream=stream).deliver([])
    assert stream.getvalue() == ""
    assert result.delivered == []


async def test_a_signal_without_an_id_is_still_printed():
    stream = io.StringIO()
    result = await TerminalSink(stream=stream).deliver([_signal(signal_id=None)])
    assert "revert" in stream.getvalue()
    assert result.delivered == []


async def test_deliver_text_writes_the_block_verbatim():
    stream = io.StringIO()
    result = await TerminalSink(stream=stream).deliver_text("Daily digest: 3 signals")
    assert stream.getvalue() == "Daily digest: 3 signals\n"
    assert result.sink == "terminal"
