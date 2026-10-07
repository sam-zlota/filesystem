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
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Protocol, Sequence, Tuple

try:  # optional native backend; see PersonMatcher.similarity
    from rapidfuzz.distance import JaroWinkler as _RapidJaroWinkler
except ImportError:  # pragma: no cover
    _RapidJaroWinkler = None


@dataclass(frozen=True)
class Normalized:
    keys: FrozenSet[str]
    fields: Tuple[Tuple[str, str], ...]  # sorted (name, value) pairs; hashable
    #: matcher-private precomputed values (not part of equality)
    data: Any = field(default=None, compare=False)

    def get(self, name: str) -> str:
        for k, v in self.fields:
            if k == name:
                return v
        return ""


class Matcher(Protocol):
    def normalize(self, attrs: Mapping[str, Any]) -> Normalized: ...
    def match(self, a: Normalized, b: Normalized) -> bool: ...

    # Optional:
    #   match_block(key, queries, members) -> [(i, j), ...]
    # returns index pairs with match(queries[i], members[j]) True.  It may
    # omit pairs that share a blocking key sorting before ``key``; the engine
    # only accepts a pair under its smallest shared key anyway.


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


def jaro_winkler(s: str, t: str, scaling: float = 0.1, max_prefix: int = 4,
                 boost_threshold: float = 0.7) -> float:
    """Winkler's definition: the common-prefix bonus applies only when the
    Jaro score exceeds ``boost_threshold``.  Identical to
    ``rapidfuzz.distance.JaroWinkler.similarity``."""
    j = jaro(s, t)
    if j <= boost_threshold:
        return j
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
_NON_DIGIT = re.compile(r"\D")
# ASCII fast paths (str.translate is several times faster than re.sub);
# non-ASCII input falls back to the regexes, so results are identical.
_ASCII_NAME = str.maketrans({c: " " for c in map(chr, range(128))
                             if not (c.isascii() and (c.isdigit() or "a" <= c <= "z"))})
_ASCII_DIGITS = str.maketrans("", "", "".join(chr(c) for c in range(128) if not chr(c).isdigit()))


def _norm_name(value: Any) -> str:
    s = str(value or "").lower()
    if s.isascii():
        return " ".join(s.translate(_ASCII_NAME).split())
    s = _NON_ALNUM.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _norm_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _digits(value: Any) -> str:
    s = str(value or "")
    return s.translate(_ASCII_DIGITS) if s.isascii() else _NON_DIGIT.sub("", s)


def _norm_phone(value: Any) -> str:
    digits = _digits(value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _norm_zip(value: Any) -> str:
    return _digits(value)[:5]


def name_similarity(a: str, b: str) -> float:
    """Jaro-Winkler, tolerant of token order ("Smith John" vs "John Smith")."""
    if not a or not b:
        return 0.0
    direct = jaro_winkler(a, b)
    sorted_a = " ".join(sorted(a.split()))
    sorted_b = " ".join(sorted(b.split()))
    return max(direct, jaro_winkler(sorted_a, sorted_b))


_BACKENDS: Dict[str, Optional[Callable[..., float]]] = {
    "python": jaro_winkler,
    "rapidfuzz": None if _RapidJaroWinkler is None else _RapidJaroWinkler.similarity,
}


@dataclass(frozen=True)
class PersonMatcher:
    """Rule-based matcher for records with name / email / phone / zip.

    A pair matches when any rule fires:
      1. identical normalized email;
      2. identical phone and name similarity >= ``phone_name_threshold``;
      3. identical zip and name similarity >= ``zip_name_threshold``.

    Name similarity is Jaro-Winkler, tolerant of token order: the larger of
    the score on the names as written and on their sorted tokens.

    Blocking keys are the email, the phone and the zip, so every pair that
    *could* match shares at least one key.

    ``zip_blocking="zip+initials"`` narrows the zip block to records whose
    name initials also agree ("John Smith" and "Smith, J." both give "js").
    Large deployments need this: a plain zip block grows with the population
    and makes every insert compare against thousands of records.  It trades
    a little recall (a typo in a first letter) for bounded block sizes.

    ``similarity`` picks the Jaro-Winkler implementation: ``"python"`` (no
    dependencies) or ``"rapidfuzz"`` (native, ~20x faster).  Both compute the
    same function, but every replica of a cluster must use the same one,
    because convergence relies on all replicas taking identical decisions.
    """

    phone_name_threshold: float = 0.80
    zip_name_threshold: float = 0.93
    zip_blocking: str = "zip"
    similarity: str = "python"

    def __post_init__(self) -> None:
        if self.similarity not in _BACKENDS:
            raise ValueError(f"unknown similarity backend {self.similarity!r}")
        fn = _BACKENDS[self.similarity]
        if fn is None:
            raise RuntimeError(f"similarity={self.similarity!r} needs: pip install {self.similarity}")
        object.__setattr__(self, "_jw", fn)
        object.__setattr__(self, "_cutoff", self.similarity == "rapidfuzz")

    def normalize(self, attrs: Mapping[str, Any]) -> Normalized:
        name = _norm_name(attrs.get("name"))
        email = _norm_email(attrs.get("email"))
        phone = _norm_phone(attrs.get("phone"))
        zip_ = _norm_zip(attrs.get("zip"))
        keys = set()
        if email:
            keys.add("email:" + email)
        if phone:
            keys.add("phone:" + phone)
        if zip_:
            tokens = name.split()
            initials = "".join(sorted(tok[0] for tok in tokens))
            if self.zip_blocking == "zip+initials" and initials:
                keys.add("zipname:" + zip_ + ":" + initials)
            else:
                keys.add("zip:" + zip_)
        sorted_name = " ".join(sorted(name.split()))
        return Normalized(
            keys=frozenset(keys),
            fields=(("email", email), ("name", name), ("phone", phone), ("zip", zip_)),
            data=(name, sorted_name, email, phone, zip_),
        )

    def match_block(self, key: str, queries: Sequence[Normalized],
                    members: Sequence[Normalized]) -> List[Tuple[int, int]]:
        """All (i, j) with ``match(queries[i], members[j])``, scored in bulk.

        With the rapidfuzz backend the name similarity of every pair is
        computed in one native call, and the exact rules then run only on
        pairs whose names could pass a threshold.  That pre-filter is lossless
        here: outside the email rule every rule needs name similarity of at
        least min(thresholds), and a pair matched by the email rule shares the
        ``email:`` key, which sorts before every other key, so it belongs to
        the email block rather than to this one.
        """
        nq, nm = len(queries), len(members)
        match = self.match
        if not self._cutoff or nq * nm < 64 or key.startswith("email:"):  # type: ignore[attr-defined]
            return [(i, j) for i, a in enumerate(queries) for j, b in enumerate(members) if match(a, b)]
        import numpy as np
        from rapidfuzz import process
        cut = min(self.phone_name_threshold, self.zip_name_threshold)
        member_names = [b.data[0] for b in members]
        member_sorted = [b.data[1] for b in members]
        out: List[Tuple[int, int]] = []
        for lo in range(0, nq, 256):  # bound the score matrix to 256 x |block|
            chunk = queries[lo:lo + 256]
            direct = process.cdist([a.data[0] for a in chunk], member_names, scorer=_RapidJaroWinkler.similarity,
                                   score_cutoff=cut, dtype=np.float64)
            swapped = process.cdist([a.data[1] for a in chunk], member_sorted, scorer=_RapidJaroWinkler.similarity,
                                    score_cutoff=cut, dtype=np.float64)
            ii, jj = np.nonzero((direct >= cut) | (swapped >= cut))
            for i, j in zip(ii.tolist(), jj.tolist()):
                if match(chunk[i], members[j]):
                    out.append((lo + i, j))
        return out

    def match(self, a: Normalized, b: Normalized) -> bool:
        an, asorted, ae, ap, az = a.data
        bn, bsorted, be, bp, bz = b.data
        if ae and ae == be:
            return True
        if not an or not bn:
            return False
        if ap and ap == bp:
            threshold = self.phone_name_threshold
        elif az and az == bz:
            threshold = self.zip_name_threshold
        else:
            return False
        jw = self._jw  # type: ignore[attr-defined]
        if self._cutoff:  # type: ignore[attr-defined]
            # score_cutoff lets rapidfuzz abandon hopeless pairs early; it
            # only changes scores below the threshold, never the decision
            if jw(an, bn, score_cutoff=threshold) >= threshold:
                return True
            return (asorted != an or bsorted != bn) and jw(asorted, bsorted, score_cutoff=threshold) >= threshold
        if jw(an, bn) >= threshold:
            return True
        return (asorted != an or bsorted != bn) and jw(asorted, bsorted) >= threshold
