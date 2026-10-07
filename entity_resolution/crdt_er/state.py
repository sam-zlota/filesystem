"""The replicated entity-resolution state: a join-semilattice.

``ERState`` is the *only* thing that is replicated.  It is the product of two
lattices, so its ``merge`` is commutative, associative and idempotent:

* ``records``   -- :class:`LWWMap` from record id to attribute dict.  A record
  update replaces the whole attribute dict (coarse-grained LWW); a delete
  writes a tombstone.
* ``assertions`` -- :class:`ORSet` of human/curator assertions about pairs:
  ``("link", a, b)`` forces ``a`` and ``b`` into the same entity,
  ``("unlink", a, b)`` forbids the automatic match between them.

The entity clustering is **not** stored.  It is a deterministic function of
the state (see :mod:`crdt_er.engine`), so convergence of the state implies
convergence of the clustering.

An ``ERState`` can also be used as a *delta*: a local operation produces a
small ERState containing only the entries it touched, which is merged into
other replicas with the same ``merge`` as a full-state sync.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Set, Tuple

from .lattice import LWWMap, ORSet, Tag, Timestamp

RecordId = str
Attrs = Mapping[str, Any]
Pair = Tuple[RecordId, RecordId]
#: (kind, a, b) with kind in {"link", "unlink"} and a < b.
Assertion = Tuple[str, RecordId, RecordId]

LINK = "link"
UNLINK = "unlink"


def pair_key(a: RecordId, b: RecordId) -> Pair:
    """Canonical (ordered) form of an unordered record pair."""
    if a == b:
        raise ValueError("a pair needs two distinct records")
    return (a, b) if a < b else (b, a)


@dataclass
class Dirty:
    """What changed in a merge: the engine only recomputes around these."""

    records: Set[RecordId] = field(default_factory=set)
    pairs: Set[Pair] = field(default_factory=set)

    def touched_records(self) -> Set[RecordId]:
        out = set(self.records)
        for a, b in self.pairs:
            out.add(a)
            out.add(b)
        return out

    def __bool__(self) -> bool:
        return bool(self.records or self.pairs)


class ERState:
    """Replicated state.  ``merge`` is the lattice join."""

    def __init__(self) -> None:
        self.records: LWWMap[RecordId, Dict[str, Any]] = LWWMap()
        self.assertions: ORSet[Assertion] = ORSet()

    # -- reads -------------------------------------------------------------
    def record(self, rid: RecordId) -> Optional[Dict[str, Any]]:
        return self.records.get(rid)

    def is_live(self, rid: RecordId) -> bool:
        return self.records.get(rid) is not None

    def live_record_ids(self) -> Set[RecordId]:
        return {rid for rid, _ in self.records.live_items()}

    def has_assertion(self, kind: str, a: RecordId, b: RecordId) -> bool:
        return (kind,) + pair_key(a, b) in self.assertions

    def assertion_partners(self, rid: RecordId) -> Set[RecordId]:
        """Records that share any live assertion with ``rid``."""
        out: Set[RecordId] = set()
        for _, a, b in self.assertions.elements():
            if a == rid:
                out.add(b)
            elif b == rid:
                out.add(a)
        return out

    def max_counter(self) -> int:
        return max(self.records.max_timestamp(), self.assertions.max_seq())

    # -- delta constructors (used by the engine) ----------------------------
    @staticmethod
    def delta_record(rid: RecordId, ts: Timestamp, attrs: Optional[Dict[str, Any]]) -> "ERState":
        d = ERState()
        d.records.set(rid, ts, None if attrs is None else dict(attrs))
        return d

    @staticmethod
    def delta_assert(tag: Tag, assertion: Assertion) -> "ERState":
        d = ERState()
        d.assertions.add(tag, assertion)
        return d

    @staticmethod
    def delta_retract(tags: Set[Tag]) -> "ERState":
        d = ERState()
        d.assertions.remove_tags(set(tags))
        return d

    # -- lattice join --------------------------------------------------------
    def merge(self, other: "ERState") -> Dirty:
        """Join ``other`` into ``self`` and report what changed."""
        dirty = Dirty()
        dirty.records = self.records.merge(other.records)
        for _, a, b in self.assertions.merge(other.assertions):
            dirty.pairs.add((a, b))
        return dirty

    # -- misc --------------------------------------------------------------
    def copy(self) -> "ERState":
        new = ERState()
        new.records = self.records.copy()
        new.assertions = self.assertions.copy()
        return new

    def to_dict(self) -> Dict[str, Any]:
        return {"records": self.records.to_dict(), "assertions": self.assertions.to_dict()}

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ERState)
            and self.records == other.records
            and self.assertions == other.assertions
        )

    def __repr__(self) -> str:
        return f"ERState(records={len(self.records)} live, assertions={self.assertions!r})"
