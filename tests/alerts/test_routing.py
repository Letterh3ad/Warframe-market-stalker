from datetime import datetime, timezone

from wfm.alerts.routing import route, route_operational
from wfm.models import Direction, Horizon, Signal

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _signal(horizon=Horizon.URGENT, confidence=0.9, magnitude=20.0) -> Signal:
    return Signal(slug="x", rank=0, analyzer="flip", ts=NOW, direction=Direction.BUY,
                  magnitude=magnitude, confidence=confidence, horizon=horizon, evidence={})


def test_terminal_always_receives_everything():
    for horizon in (Horizon.URGENT, Horizon.DAILY):
        assert "terminal" in route(_signal(horizon=horizon), discord_configured=True,
                                   min_confidence=0.6, min_magnitude=0.0)
        assert "terminal" in route(_signal(horizon=horizon), discord_configured=False,
                                   min_confidence=0.6, min_magnitude=0.0)


def test_an_urgent_signal_above_the_thresholds_also_goes_to_discord():
    assert route(_signal(), discord_configured=True, min_confidence=0.6,
                 min_magnitude=0.0) == {"terminal", "discord"}


def test_an_urgent_signal_below_the_confidence_threshold_stays_local():
    assert route(_signal(confidence=0.2), discord_configured=True, min_confidence=0.6,
                 min_magnitude=0.0) == {"terminal"}


def test_an_urgent_signal_below_the_magnitude_threshold_stays_local():
    assert route(_signal(magnitude=1.0), discord_configured=True, min_confidence=0.6,
                 min_magnitude=5.0) == {"terminal"}


def test_alert_override_forces_discord_regardless_of_thresholds():
    assert route(_signal(confidence=0.1), discord_configured=True, min_confidence=0.9,
                 min_magnitude=99.0, alert_override=True) == {"terminal", "discord"}


def test_a_daily_signal_is_never_sent_live_to_discord():
    assert route(_signal(horizon=Horizon.DAILY), discord_configured=True, min_confidence=0.0,
                 min_magnitude=0.0) == {"terminal"}


def test_a_daily_signal_with_an_override_is_sent_live():
    assert route(_signal(horizon=Horizon.DAILY), discord_configured=True, min_confidence=0.0,
                 min_magnitude=0.0, alert_override=True) == {"terminal", "discord"}


def test_without_a_webhook_nothing_routes_to_discord():
    assert route(_signal(), discord_configured=False, min_confidence=0.0, min_magnitude=0.0,
                 alert_override=True) == {"terminal"}


def test_operational_alerts_go_everywhere_configured():
    assert route_operational(discord_configured=True) == {"terminal", "discord"}
    assert route_operational(discord_configured=False) == {"terminal"}
