"""Normalization and pairwise scoring (pure, deterministic functions)."""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Mapping

from rapidfuzz import fuzz

from .config import FieldRule, ResolverConfig

_NON_WORD = re.compile(r"[^\w\s@.+]")


def normalize(value: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = _NON_WORD.sub(" ", value.casefold())
    return " ".join(value.split())


def phone_key(value: str) -> str:
    """Last 10 digits, or '' when there are too few digits to be meaningful."""
    digits = "".join(c for c in value if c.isdigit())
    return digits[-10:] if len(digits) >= 7 else ""


def _exact(a: str, b: str) -> float:
    return 1.0 if normalize(a) == normalize(b) else 0.0


def _phone(a: str, b: str) -> float:
    ka, kb = phone_key(a), phone_key(b)
    return 1.0 if ka and ka == kb else 0.0


def _token_sort(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(normalize(a), normalize(b)) / 100.0


def _token_set(a: str, b: str) -> float:
    return fuzz.token_set_ratio(normalize(a), normalize(b)) / 100.0


COMPARATORS: dict[str, Callable[[str, str], float]] = {
    "exact": _exact,
    "phone": _phone,
    "token_sort": _token_sort,
    "token_set": _token_set,
}


def _level(rule: FieldRule, raw: float) -> float:
    if rule.hi <= rule.lo:
        return raw
    return min(1.0, max(0.0, (raw - rule.lo) / (rule.hi - rule.lo)))


def score(a: Mapping[str, str], b: Mapping[str, str], config: ResolverConfig) -> float:
    """Additive log-likelihood-style match score. Symmetric by construction
    of the caller (the engine always passes records in canonical id order)."""
    total = 0.0
    for rule in config.rules:
        va, vb = a.get(rule.field), b.get(rule.field)
        if not va or not vb:
            continue
        level = _level(rule, COMPARATORS[rule.comparator](va, vb))
        total += rule.disagree + level * (rule.agree - rule.disagree)
    return round(total, config.score_decimals)
