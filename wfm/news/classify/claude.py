"""Classification through the official anthropic SDK.

Two jobs: a fallback when no local model is available, and the labeller that
produces the benchmark's gold set — which is the reason the two-backend seam earns
its keep beyond redundancy.

`anthropic` is an optional extra, so the import happens in __init__ rather than at
module level: the architecture test imports every module in this package and must
not need the SDK to do it.
"""

from __future__ import annotations

import json

from wfm.news.classify.base import ClassifierError
from wfm.news.schema import OUTPUT_SCHEMA, SYSTEM_PROMPT, build_prompt, decode
from wfm.news.types import ClassifyLabels, ClassifyRequest

DEFAULT_MODEL = "claude-haiku-4-5"

# One label set with a one-sentence rationale. Structured output leaves no room for
# preamble, so this is generous already.
DEFAULT_MAX_TOKENS = 512


class ClaudeClassifier:
    name = "claude"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client=None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.version = model
        self._model = model
        self._max_tokens = max_tokens
        if client is not None:
            self._client = client
            return
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise ClassifierError(
                "the claude backend needs the optional dependency: "
                "pip install 'wfm[news]'"
            ) from exc
        self._client = anthropic.AsyncAnthropic()

    async def classify(self, req: ClassifyRequest) -> ClassifyLabels:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        # Identical on every call in a backfill of thousands.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": build_prompt(req)}],
                output_config={"format": OUTPUT_SCHEMA},
            )
        except Exception as exc:  # the SDK's error tree is not importable here
            raise ClassifierError(f"claude {self._model}: {exc}") from exc

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ClassifierError(f"claude {self._model} returned no text block")
        try:
            return decode(json.loads(text))
        except (ValueError, TypeError) as exc:
            raise ClassifierError(f"claude {self._model} answered {text!r}") from exc
