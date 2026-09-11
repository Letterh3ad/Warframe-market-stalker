from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum


class NewsSource(str, Enum):
    WARFRAME_NEWS = "warframe_news"
    FORUMS = "forums"
    REDDIT = "reddit"


class EventType(str, Enum):
    VAULT_IN = "vault_in"
    VAULT_OUT = "vault_out"
    PRIME_ACCESS = "prime_access"
    BUFF = "buff"
    NERF = "nerf"
    REWORK = "rework"
    DROP_RATE_CHANGE = "drop_rate_change"
    NEW_CONTENT = "new_content"
    OTHER = "other"


class NewsDirection(str, Enum):
    """Expected price direction, deliberately separate from models.Direction.

    models.Direction is a trade instruction (BUY/SELL/HOLD). This says where a price
    is expected to move, which is a different claim: a price going up is a reason to
    buy and a reason not to sell, and collapsing the two loses that.
    """

    UP = "up"
    DOWN = "down"
    UNCLEAR = "unclear"

    @property
    def sign(self) -> int:
        return _SIGNS[self.value]


_SIGNS = {"up": 1, "down": -1, "unclear": 0}


class ArticleStatus(str, Enum):
    PENDING = "pending"
    CLASSIFIED = "classified"
    NO_MATCH = "no_match"
    FAILED = "failed"


class LinkMethod(str, Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    SET_EXPANSION = "set_expansion"
    CURATED = "curated"
    # The slug is a prediction: an announced Prime the catalog does not sell yet.
    # Replaced by a real link once `wfm sync` sees the slug appear.
    SYNTHETIC = "synthetic"
    # Reached through a base warframe name ("Banshee" -> banshee_prime_set), which
    # is weaker evidence than the article writing "Banshee Prime".
    BASE_ALIAS = "base_alias"


@dataclass(frozen=True)
class Article:
    """`body` is never persisted. The gate reads it to find candidates and produce an
    excerpt, then it is dropped: storing full article text is not this feature's job.
    """

    source: NewsSource
    external_id: str
    url: str
    title: str
    body: str = ""
    published_at: datetime | None = None
    excerpt: str | None = None
    status: ArticleStatus = ArticleStatus.PENDING
    content_hash: str = ""
    classifier_name: str | None = None
    classifier_version: str | None = None
    classified_at: datetime | None = None
    id: int | None = None

    def hashed(self) -> Article:
        """A copy carrying the hash of the CURRENT title and body.

        Deliberately not a property. An article loaded back from the database has
        body="" (bodies are never persisted), so a computed property could never match
        the stored hash and every re-fetch would look like an edit. Sources call this
        once, at fetch time, while the body is still in hand.
        """
        return replace(self, content_hash=content_hash(self.title, self.body))


@dataclass(frozen=True)
class ExtractedEvent:
    """One classified event.

    `strength` and `confidence` are already-mapped numbers, not raw model output. The
    classifier emits coarse labels (minor/moderate/major, low/medium/high) because small
    models cannot produce calibrated floats, and maps them through config before building
    this. Nothing in this plan does that mapping; it arrives with the classifier.
    """

    event_type: EventType
    subject_raw: str
    direction: NewsDirection
    strength: float
    confidence: float
    rationale: str | None = None
    effective_at: datetime | None = None
    raw_json: str | None = None
    article_id: int | None = None
    id: int | None = None


@dataclass(frozen=True)
class ItemLink:
    slug: str
    rank: int
    link_method: LinkMethod
    link_score: float
    direction: NewsDirection
    weight: float
    event_id: int | None = None


@dataclass(frozen=True)
class LinkedEvent:
    """One fully joined link row: everything the bias math and the audit payload need,
    so neither has to go back to the database.
    """

    slug: str
    rank: int
    event_type: EventType
    direction: NewsDirection
    strength: float
    confidence: float
    weight: float
    link_method: LinkMethod
    subject_raw: str
    effective_at: datetime | None
    published_at: datetime | None
    article_id: int
    article_title: str
    article_url: str
    excerpt: str | None = None


@dataclass(frozen=True)
class Candidate:
    """A catalog item the fuzzy gate found in article text, with surrounding context.

    `context` is what the classifier is later shown, so the model classifies one
    subject in its own sentences rather than a whole article at once.
    """

    slug: str
    name: str
    score: float
    context: str
    start: int
    end: int


@dataclass(frozen=True)
class ClassifyRequest:
    """One candidate, its surrounding sentences, and the article's publish date.

    One request is one model call. Handing a small model a whole hotfix note and
    asking for a list of events is hard extraction and it is unreliable at that; the
    gate already knows which item is named and where, which turns the job into
    constrained classification of a single subject.
    """

    subject: str
    context: str
    published_at: datetime | None


@dataclass(frozen=True)
class ClassifyLabels:
    """Exactly what a model is allowed to say. All strings, on purpose.

    Small models cannot produce calibrated probabilities: asked for a 0-to-1
    confidence a 4B answers 0.8 for almost everything. Asked to pick one of three
    labels it is reliable. Code maps labels to numbers through config, so retuning
    that mapping after the backtest reclassifies nothing.
    """

    event_type: str
    direction: str
    strength: str
    confidence: str
    timing: str
    date_text: str | None = None
    rationale: str | None = None


def content_hash(title: str, body: str) -> str:
    """Detects an edited article so it can be re-queued for classification.

    The NUL separator keeps ("ab", "c") from hashing the same as ("a", "bc").
    """
    payload = f"{title}\x00{body}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
