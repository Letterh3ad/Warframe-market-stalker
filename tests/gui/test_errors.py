from fastapi import FastAPI
from fastapi.testclient import TestClient

from wfm.gui.errors import install_error_handlers
from wfm.services.errors import ApiError, CircuitOpen


def _app_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise exc

    return TestClient(app)


def test_circuit_open_maps_to_503():
    response = _app_raising(CircuitOpen("3 consecutive 429 responses")).get("/boom")
    assert response.status_code == 503
    assert response.json() == {"error": "3 consecutive 429 responses"}


def test_api_error_maps_to_502():
    response = _app_raising(ApiError("gave up after 5 attempts")).get("/boom")
    assert response.status_code == 502


def test_lookup_error_maps_to_404():
    response = _app_raising(LookupError("no catalog item matches 'x'")).get("/boom")
    assert response.status_code == 404


def test_key_error_also_maps_to_404():
    # KeyError subclasses LookupError (e.g. GroupsRepo._require raises KeyError);
    # no separate handler is registered for it, the LookupError one must still catch it.
    response = _app_raising(KeyError("no such group: mods")).get("/boom")
    assert response.status_code == 404


def test_value_error_maps_to_400():
    response = _app_raising(ValueError("quantity must be positive")).get("/boom")
    assert response.status_code == 400
