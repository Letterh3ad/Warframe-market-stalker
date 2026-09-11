import json
from datetime import datetime, timezone

import pytest

from wfm.news.classify.base import ClassifierError
from wfm.news.classify.claude import ClaudeClassifier
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


class Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class Response:
    def __init__(self, text):
        self.content = [Block(text)]


class StubMessages:
    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raises is not None:
            raise self.raises
        return self.response


class StubClient:
    def __init__(self, messages):
        self.messages = messages
        self.closed = False

    async def close(self):
        self.closed = True


async def test_it_sends_the_schema_as_output_config_and_caches_the_system_prompt():
    messages = StubMessages(Response(json.dumps(ANSWER)))
    clf = ClaudeClassifier(client=StubClient(messages))
    await clf.classify(REQ)

    kwargs = messages.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["output_config"]["format"]["schema"]["additionalProperties"] is False
    # The system prompt is identical on every one of thousands of calls; caching it
    # is most of the cost of this backend.
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Mesa Prime" in kwargs["messages"][0]["content"]


async def test_it_decodes_the_answer():
    clf = ClaudeClassifier(client=StubClient(StubMessages(Response(json.dumps(ANSWER)))))
    labels = await clf.classify(REQ)
    assert labels.event_type == "vault_in"
    assert labels.rationale == "Supply is cut off."


async def test_the_model_is_overridable_for_the_gold_set_run():
    messages = StubMessages(Response(json.dumps(ANSWER)))
    clf = ClaudeClassifier(model="claude-opus-5", client=StubClient(messages))
    await clf.classify(REQ)
    assert messages.kwargs["model"] == "claude-opus-5"
    assert clf.version == "claude-opus-5"


async def test_an_sdk_failure_becomes_a_classifier_error():
    clf = ClaudeClassifier(client=StubClient(StubMessages(raises=RuntimeError("429"))))
    with pytest.raises(ClassifierError):
        await clf.classify(REQ)


async def test_a_response_with_no_text_block_becomes_a_classifier_error():
    empty = Response(json.dumps(ANSWER))
    empty.content = []
    clf = ClaudeClassifier(client=StubClient(StubMessages(empty)))
    with pytest.raises(ClassifierError):
        await clf.classify(REQ)


async def test_an_out_of_enum_answer_becomes_a_classifier_error():
    bad = json.dumps(dict(ANSWER, direction="sideways"))
    clf = ClaudeClassifier(client=StubClient(StubMessages(Response(bad))))
    with pytest.raises(ClassifierError):
        await clf.classify(REQ)


def test_importing_the_module_does_not_require_the_sdk():
    # The architecture test imports every module in wfm/news; `anthropic` is an
    # optional extra and is not installed in the dev venv.
    import wfm.news.classify.claude  # noqa: F401


async def test_it_closes_the_sdk_client_like_the_ollama_backend_does():
    # news_service closes a backend it owns behind hasattr(classifier, "aclose"),
    # so a missing method is a silent leak rather than an error.
    client = StubClient(StubMessages(Response(json.dumps(ANSWER))))
    clf = ClaudeClassifier(client=client)
    async with clf:
        await clf.classify(REQ)
    assert client.closed is True


async def test_closing_a_client_without_close_is_not_an_error():
    class Bare:
        messages = StubMessages(Response(json.dumps(ANSWER)))

    await ClaudeClassifier(client=Bare()).aclose()
