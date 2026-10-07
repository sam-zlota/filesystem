"""Pluggable, deterministic pairwise matching with blocking.

The engine only needs two things from a matcher:

* ``normalize(attrs) -> Normalized``: canonical fields plus a set of
  *blocking keys*.  Two records are only compared if they share a key.
* ``match(a, b) -> bool``: a symmetric, pure decision on two normalized
  records.

Determinism is what makes the system a CRDT: because every replica evaluates
the same pure function on the same converged attribute values, every replica
derives the same match edges.  (All replicas must run the same matcher
version; that is a deployment invariant, not something the lattice enforces.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, FrozenSet, Mapping, Protocol, Tuple


@dataclass(frozen=True)
class Normalized:
    keys: FrozenSet[str]
    fields: Tuple[Tuple[str, str], ...]  # sorted (name, value) pairs; hashable

    def get(self, name: str) -> str:
        for k, v in self.fields:
            if k == name:
                return v
        return ""


class Matcher(Protocol):
    def normalize(self, attrs: Mapping[str, Any]) -> Normalized: ...
    def match(self, a: Normalized, b: Normalized) -> bool: ...


# ---------------------------------------------------------------------------
# String similarity
# ---------------------------------------------------------------------------
def jaro(s: str, t: str) -> float:
    if s == t:
        return 1.0
    ls, lt = len(s), len(t)
    if not ls or not lt:
        return 0.0
    window = max(0, max(ls, lt) // 2 - 1)
    s_matched = [False] * ls
    t_matched = [False] * lt
    matches = 0
    for i, ch in enumerate(s):
        lo, hi = max(0, i - window), min(lt, i + window + 1)
        for j in range(lo, hi):
            if not t_matched[j] and t[j] == ch:
                s_matched[i] = t_matched[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(ls):
        if s_matched[i]:
            while not t_matched[k]:
                k += 1
            if s[i] != t[k]:
                transpositions += 1
            k += 1
    transpositions //= 2
    return (matches / ls + matches / lt + (matches - transpositions) / matches) / 3


def jaro_winkler(s: str, t: str, scaling: float = 0.1, max_prefix: int = 4) -> float:
    j = jaro(s, t)
    prefix = 0
    for a, b in zip(s, t):
        if a != b or prefix == max_prefix:
            break
        prefix += 1
    return j + prefix * scaling * (1.0 - j)


# ---------------------------------------------------------------------------
# A concrete matcher for person-like records
# ---------------------------------------------------------------------------
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def _norm_name(value: Any) -> str:
    s = _NON_ALNUM.sub(" ", str(value or "").lower())
    return _WS.sub(" ", s).strip()


def _norm_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _norm_zip(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))[:5]


def name_similarity(a: str, b: str) -> float:
    """Jaro-Winkler, tolerant of token order ("Smith John" vs "John Smith")."""
    if not a or not b:
        return 0.0
    direct = jaro_winkler(a, b)
    sorted_a = " ".join(sorted(a.split()))
    sorted_b = " ".join(sorted(b.split()))
    return max(direct, jaro_winkler(sorted_a, sorted_b))


@dataclass(frozen=True)
class PersonMatcher:
    """Rule-based matcher for records with name / email / phone / zip.

    A pair matches when any rule fires:
      1. identical normalized email;
      2. identical phone and name similarity >= ``phone_name_threshold``;
      3. identical zip and name similarity >= ``zip_name_threshold``.

    Blocking keys are the email, the phone and the zip, so every pair that
    *could* match shares at least one key.

    ``zip_blocking="zip+initials"`` narrows the zip block to records whose
    name initials also agree ("John Smith" and "Smith, J." both give "js").
    Large deployments need this: a plain zip block grows with the population
    and makes every insert compare against thousands of records.  It trades
    a little recall (a typo in a first letter) for bounded block sizes.
    """

    phone_name_threshold: float = 0.80
    zip_name_threshold: float = 0.93
    zip_blocking: str = "zip"

    def normalize(self, attrs: Mapping[str, Any]) -> Normalized:
        fields = {
            "name": _norm_name(attrs.get("name")),
            "email": _norm_email(attrs.get("email")),
            "phone": _norm_phone(attrs.get("phone")),
            "zip": _norm_zip(attrs.get("zip")),
        }
        keys = set()
        if fields["email"]:
            keys.add("email:" + fields["email"])
        if fields["phone"]:
            keys.add("phone:" + fields["phone"])
        if fields["zip"]:
            initials = "".join(sorted(tok[0] for tok in fields["name"].split()))
            if self.zip_blocking == "zip+initials" and initials:
                keys.add("zipname:" + fields["zip"] + ":" + initials)
            else:
                keys.add("zip:" + fields["zip"])
        return Normalized(keys=frozenset(keys), fields=tuple(sorted(fields.items())))

    def match(self, a: Normalized, b: Normalized) -> bool:
        if a.get("email") and a.get("email") == b.get("email"):
            return True
        sim = name_similarity(a.get("name"), b.get("name"))
        if a.get("phone") and a.get("phone") == b.get("phone") and sim >= self.phone_name_threshold:
            return True
        if a.get("zip") and a.get("zip") == b.get("zip") and sim >= self.zip_name_threshold:
            return True
        return False
