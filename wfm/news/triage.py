"""Which candidates are worth a model call.

Per-candidate prompting turned one 37KB update note into 56 candidates, i.e. 56 model
calls, most of them for bug-fix lines that mention a mod in passing. A score floor
cannot help: the gate scores an exact token match 1.0 and essentially all 56 are
exact, so any floor either keeps everything or nothing. The discriminator has to be
the context, not the score.

Two stages. Candidates whose context shows no event-shaped language are dropped
outright; survivors are ordered by how much evidence they carry and truncated to a
hard cap, so a pathological article cannot spend an unbounded budget. The cap alone
would be worse than useless: with scores tied at 1.0 the gate's own order is
alphabetical, so a bare cap drops real events to keep Adaptation.

The cost is recall on real events phrased without any keyword here. That cost is
measured in the design doc (## Triage measurement 2026-09-09), and the fix when it
goes wrong is a keyword, not a redesign.
"""

from __future__ import annotations

from wfm.news.types import Candidate

SIGNAL_GROUPS: dict[str, tuple[str, ...]] = {
    "vault": ("vault", "unvault", "resurgence"),
    "release": (
        "prime access",
        "now available",
        "arrives",
        "launch",
        "introducing",
        "released",
        "release",
    ),
    "balance": (
        "increase",
        "decrease",
        "reduced",
        "buff",
        "nerf",
        "adjust",
        "rebalance",
        "improved",
        "no longer",
        "now deals",
        "now grants",
    ),
    "drop": ("drop rate", "drop chance", "drop table", "rarity", "refinement"),
    "rework": ("rework", "revisit", "overhaul", "changes to"),
}


def signal_strength(context: str) -> int:
    """How many distinct kinds of event evidence the context shows.

    Distinct groups, not occurrences: three vault words is still one kind of
    evidence, and counting hits would rank a repetitive sentence above a
    corroborated one.
    """
    lowered = context.lower()
    return sum(
        any(keyword in lowered for keyword in keywords)
        for keywords in SIGNAL_GROUPS.values()
    )


def triage(candidates: list[Candidate], cap: int) -> tuple[list[Candidate], int]:
    """(candidates worth classifying, how many were skipped)."""
    scored = [(signal_strength(c.context), c) for c in candidates]
    survivors = [(signal, c) for signal, c in scored if signal > 0]
    survivors.sort(key=lambda pair: (-pair[0], -pair[1].score, pair[1].slug))
    kept = [c for _, c in survivors[: max(cap, 0)]]
    return kept, len(candidates) - len(kept)
