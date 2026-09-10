import json
from datetime import datetime, timezone

import httpx
import pytest

from wfm.news.classify.base import ClassifierError
from wfm.news.classify.ollama import OllamaClassifier
from wfm.news.types import ClassifyRequest

PUB = datetime(2026, 9, 6, tzinfo=timezone.utc)
REQ = ClassifyRequest(
    subject="Mesa Prime",
    context="Mesa Prime enters the Prime Vault on September 20th.",
    published_at=PUB,
)

ANSWER = {
    "event_type": "vault_in",
    "direction": "up",
    "strength": "major",
    "confidence": "high",
    "timing": "dated",
    "date_text": "September 20",
    "rationale": "Supply is cut off.",
}


def transport(handler):
    return httpx.MockTransport(handler)


async def test_it_posts_the_schema_the_prompt_and_a_zero_temperature():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"message": {"content": json.dumps(ANSWER)}})

    async with OllamaClassifier("qwen3:4b", transport=transport(handler)) as clf:
        await clf.classify(REQ)

    assert seen["url"] == "http://localhost:11434/api/chat"
    assert seen["model"] == "qwen3:4b"
    assert seen["stream"] is False
    assert seen["options"]["temperature"] == 0
    assert seen["format"]["properties"]["event_type"]["enum"][0] == "vault_in"
    assert "Mesa Prime" in seen["messages"][-1]["content"]
    assert "2026-09-06" in seen["messages"][-1]["content"]


async def test_it_decodes_the_answer_into_labels():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": json.dumps(ANSWER)}})

    async with OllamaClassifier("qwen3:4b", transport=transport(handler)) as clf:
        labels = await clf.classify(REQ)

    assert labels.event_type == "vault_in"
    assert labels.date_text == "September 20"


async def test_the_name_and_version_identify_the_model():
    async with OllamaClassifier("qwen3:4b", transport=transport(lambda r: None)) as clf:
        assert clf.name == "ollama"
        assert clf.version == "qwen3:4b"


async def test_a_refused_connection_becomes_a_classifier_error():
    # Ollama not running is the expected failure on this machine, and it has to
    # degrade to status='failed' rather than crash ingest.
    def handler(request):
        raise httpx.ConnectError("connection refused")

    async with OllamaClassifier("qwen3:4b", transport=transport(handler)) as clf:
        with pytest.raises(ClassifierError):
            await clf.classify(REQ)


@pytest.mark.parametrize("status", [404, 500])
async def test_a_bad_status_becomes_a_classifier_error(status):
    async with OllamaClassifier(
        "qwen3:4b", transport=transport(lambda r: httpx.Response(status, text="nope"))
    ) as clf:
        with pytest.raises(ClassifierError):
            await clf.classify(REQ)


async def test_a_non_json_content_becomes_a_classifier_error():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "I think it vaults."}})

    async with OllamaClassifier("qwen3:4b", transport=transport(handler)) as clf:
        with pytest.raises(ClassifierError):
            await clf.classify(REQ)


async def test_an_out_of_enum_answer_becomes_a_classifier_error():
    bad = dict(ANSWER, event_type="vaulting")

    def handler(request):
        return httpx.Response(200, json={"message": {"content": json.dumps(bad)}})

    async with OllamaClassifier("qwen3:4b", transport=transport(handler)) as clf:
        with pytest.raises(ClassifierError):
            await clf.classify(REQ)
