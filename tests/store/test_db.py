from datetime import datetime, timedelta, timezone

import pytest

from wfm.store.db import to_utc_iso


def test_aware_non_utc_datetime_converts_to_utc_and_round_trips():
    ts = datetime(2026, 8, 27, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    iso = to_utc_iso(ts)
    assert iso == "2026-08-27T12:00:00+00:00"
    assert datetime.fromisoformat(iso) == ts


def test_naive_datetime_raises():
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 8, 27, 12, 0))
