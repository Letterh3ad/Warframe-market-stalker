import pytest

from tests.fakes.api import StubClient
from wfm.api.errors import ApiError
from wfm.sync.budget import Priority


async def test_matches_by_substring_and_records_calls():
    client = StubClient({"/versions": {"items": "v1"}})
    assert await client.get_json("https://api/v2/versions") == {"items": "v1"}
    assert client.calls[0][1] is Priority.BACKGROUND


async def test_unknown_url_fails_loudly():
    client = StubClient({})
    with pytest.raises(AssertionError):
        await client.get_json("https://api/v2/items")


async def test_scripted_errors_are_raised():
    client = StubClient({}, errors={"/items": ApiError("boom")})
    with pytest.raises(ApiError):
        await client.get_json("https://api/v2/items")
