"""Resolver configuration.

Every replica MUST run with an identical config: the resolved view is a pure
function of (replicated state, config). The fingerprint is written into the
replicated document so a mismatch is detected on sync instead of silently
producing divergent views.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldRule:
    """Fellegi-Sunter style comparison rule for one field.

    The comparator yields an agreement level in [0, 1]; the field contributes
    ``disagree + level * (agree - disagree)`` to the pair's total score.
    Missing values (on either side) contribute 0 - absence is not evidence.
    """

    field: str
    comparator: str  # one of similarity.COMPARATORS
    agree: float
    disagree: float
    # For fuzzy comparators: raw similarity <= lo -> level 0, >= hi -> level 1.
    lo: float = 0.0
    hi: float = 1.0


@dataclass(frozen=True)
class ResolverConfig:
    rules: tuple[FieldRule, ...] = (
        FieldRule("name", "token_sort", agree=4.0, disagree=-4.0, lo=0.70, hi=0.95),
        FieldRule("email", "exact", agree=7.0, disagree=-1.0),
        FieldRule("phone", "phone", agree=6.0, disagree=-1.0),
        FieldRule("address", "token_set", agree=3.0, disagree=-0.5, lo=0.70, hi=0.95),
    )
    match_threshold: float = 6.0
    # Scores are rounded so that the threshold decision is not sensitive to
    # last-bit floating point differences between platforms.
    score_decimals: int = 3

    # Blocking: MinHash LSH over character shingles of these fields ...
    lsh_fields: tuple[str, ...] = ("name",)
    lsh_threshold: float = 0.4
    lsh_num_perm: int = 64
    lsh_seed: int = 1
    shingle_size: int = 3
    # ... plus exact-key blocking on normalized identifiers.
    exact_block_fields: tuple[str, ...] = ("email", "phone")

    def fingerprint(self) -> str:
        # repr of a frozen dataclass of primitives is stable across processes.
        return hashlib.sha256(repr(self).encode()).hexdigest()[:16]


DEFAULT_CONFIG = ResolverConfig()
