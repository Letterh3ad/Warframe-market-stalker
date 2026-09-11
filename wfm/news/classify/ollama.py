"""Local classification through Ollama's /api/chat.

Adds no dependency: httpx is already required. The schema goes in as `format`, so
Ollama constrains decoding and the model cannot answer outside the enums — the
failure mode this defends against is the backend being absent, not malformed.

Its own AsyncClient rather than NewsFetcher: that fetcher is GET-only and paced for
public upstreams, and localhost needs neither.
"""

from __future__ import annotations

import json

import httpx

from wfm.news.classify.base import ClassifierError
from wfm.news.schema import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_prompt, decode
from wfm.news.types import ClassifyLabels, ClassifyRequest

# A cold model load on 8GB of VRAM is slow; a per-candidate call afterwards is not.
DEFAULT_TIMEOUT_S = 120.0


class OllamaClassifier:
    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.version = model
        self._model = model
        self._url = base_url.rstrip("/") + "/api/chat"
        self._http = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def __aenter__(self) -> OllamaClassifier:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def classify(self, req: ClassifyRequest) -> ClassifyLabels:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(req)},
            ],
            "format": OUTPUT_SCHEMA["schema"],
            "stream": False,
            # Classification, not writing. Sampling here only adds variance between
            # runs of the same corpus, which would make the benchmark meaningless.
            "options": {"temperature": 0},
        }
        try:
            response = await self._http.post(self._url, json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ClassifierError(f"ollama {self._model}: {exc}") from exc

        try:
            return decode(json.loads(content))
        except (ValueError, TypeError) as exc:
            raise ClassifierError(f"ollama {self._model} answered {content!r}") from exc
