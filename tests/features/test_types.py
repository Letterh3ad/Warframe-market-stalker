from datetime import datetime, timezone

from wfm.features.types import FeatureSet, PriceFeatures, Provenance

TS = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


def test_provenance_reports_coverage():
    prov = Provenance(samples={"price_90d": 90, "book": 731},
                      available=frozenset({"price", "book"}))
    assert prov.covers("price") is True
    assert prov.covers("price", "book") is True
    assert prov.covers("price", "seasonality") is False
    assert "seasonality" in prov.missing


def test_feature_set_has_checks_availability_not_truthiness():
    fs = FeatureSet(
        slug="x",
        rank=0,
        ts=TS,
        price=PriceFeatures(median_90d=40.0, robust_z=0.0),
        provenance=Provenance(samples={"price_90d": 90}, available=frozenset({"price"})),
    )
    assert fs.has("price") is True
    assert fs.has("book") is False
    assert fs.price.robust_z == 0.0


def test_feature_set_serializes_to_plain_json_types():
    fs = FeatureSet(
        slug="x",
        rank=0,
        ts=TS,
        price=PriceFeatures(median_90d=40.0),
        provenance=Provenance(samples={"price_90d": 90}, available=frozenset({"price"})),
    )
    payload = fs.to_dict()
    assert payload["slug"] == "x"
    assert payload["price"]["median_90d"] == 40.0
    assert payload["provenance"]["available"] == ["price"]
    assert isinstance(payload["ts"], str)


def test_an_empty_feature_set_is_legal_and_advertises_nothing():
    fs = FeatureSet(slug="x", rank=0, ts=TS)
    assert fs.has("price") is False
    assert fs.provenance.samples == {}
