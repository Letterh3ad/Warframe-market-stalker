from datetime import datetime, timedelta, timezone

from wfm.models import Direction, Horizon, Signal
from wfm.store.signals import SignalsRepo

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


def _signal(analyzer="flip", ts=NOW, expires_in=timedelta(minutes=20), horizon=Horizon.URGENT):
    return Signal(
        slug="x", rank=0, analyzer=analyzer, ts=ts, direction=Direction.BUY,
        magnitude=12.0, confidence=0.9, evidence={"fair_value": 52.5},
        horizon=horizon, expires_at=ts + expires_in if expires_in else None,
    )


def test_insert_returns_an_id_and_round_trips_evidence(conn):
    repo = SignalsRepo(conn)
    signal_id = repo.insert(_signal())
    stored = repo.query()[0]
    assert stored.id == signal_id
    assert stored.evidence == {"fair_value": 52.5}
    assert stored.direction is Direction.BUY
    assert stored.horizon is Horizon.URGENT


def test_open_for_excludes_expired_signals(conn):
    repo = SignalsRepo(conn)
    repo.insert(_signal())
    assert len(repo.open_for("x", 0, "flip", now=NOW + timedelta(minutes=5))) == 1
    assert repo.open_for("x", 0, "flip", now=NOW + timedelta(minutes=25)) == []


def test_undelivered_filters_by_horizon_and_alerted_at(conn):
    repo = SignalsRepo(conn)
    urgent_id = repo.insert(_signal(horizon=Horizon.URGENT))
    repo.insert(_signal(analyzer="revert", horizon=Horizon.DAILY, expires_in=None))
    assert [s.id for s in repo.undelivered(Horizon.URGENT)] == [urgent_id]
    assert repo.mark_alerted([urgent_id], when=NOW) == 1
    assert repo.undelivered(Horizon.URGENT) == []


def test_mark_alerted_is_idempotent(conn):
    repo = SignalsRepo(conn)
    signal_id = repo.insert(_signal())
    assert repo.mark_alerted([signal_id], when=NOW) == 1
    assert repo.mark_alerted([signal_id], when=NOW + timedelta(hours=1)) == 0


def test_query_filters(conn):
    repo = SignalsRepo(conn)
    repo.insert(_signal(analyzer="flip", ts=NOW - timedelta(days=2)))
    repo.insert(_signal(analyzer="revert", ts=NOW))
    assert len(repo.query(since=NOW - timedelta(days=1))) == 1
    assert [s.analyzer for s in repo.query(analyzer="flip")] == ["flip"]
    assert repo.query(slug="other") == []


def test_last_signal_at_supports_cooldowns(conn):
    repo = SignalsRepo(conn)
    assert repo.last_signal_at("x", 0, "flip") is None
    repo.insert(_signal())
    assert repo.last_signal_at("x", 0, "flip") == NOW


def test_non_utc_timezone_query_param_matches_utc_equivalent(conn):
    # to_utc_iso normalizes stored timestamps to UTC. A query parameter must be
    # normalized the same way, or an aware non-UTC datetime compares wrong against
    # the stored UTC string and silently drops or includes the wrong rows.
    repo = SignalsRepo(conn)
    repo.insert(_signal(analyzer="flip", ts=NOW - timedelta(days=2)))
    repo.insert(_signal(analyzer="revert", ts=NOW))
    utc_since = NOW - timedelta(days=1)
    non_utc_since = utc_since.astimezone(timezone(timedelta(hours=5)))
    assert non_utc_since.utcoffset() != utc_since.utcoffset()
    assert repo.query(since=non_utc_since) == repo.query(since=utc_since)
