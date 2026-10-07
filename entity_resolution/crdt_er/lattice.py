"""Join-semilattice building blocks used by the entity-resolution state.

Everything here is a *state-based* CRDT: each structure has a ``merge`` that is
commutative, associative and idempotent, so replicas that have observed the
same set of updates (in any order, with any duplication) hold identical state.

Two primitives are provided:

* :class:`LWWMap` -- last-writer-wins map.  Each key carries a totally ordered
  timestamp ``(lamport, replica_id)``; join is the pointwise maximum.  A value
  of ``None`` is a tombstone, so deletes are ordinary writes.
* :class:`ORSet` -- observed-remove set.  Every add gets a globally unique tag;
  a remove tombstones exactly the tags that the remover had observed.  Join is
  the union of adds and the union of removed tags.  Concurrent add/remove of
  the same element resolves add-wins.
"""
from __future__ import annotations

from typing import Any, Dict, Generic, Hashable, Iterator, Optional, Set, Tuple, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
E = TypeVar("E", bound=Hashable)

#: Totally ordered timestamp: (lamport counter, replica id).  Tuple comparison
#: gives a deterministic winner even for equal counters.
Timestamp = Tuple[int, str]
#: Globally unique tag for an ORSet add: (replica id, per-replica sequence).
Tag = Tuple[str, int]


class LWWMap(Generic[K, V]):
    """Last-writer-wins map with tombstones.

    Invariant: ``_entries[k] = (ts, value)`` where ``value is None`` marks a
    tombstone.  ``merge`` keeps, per key, the entry with the greater timestamp.
    """

    def __init__(self) -> None:
        self._entries: Dict[K, Tuple[Timestamp, Optional[V]]] = {}

    # -- reads -------------------------------------------------------------
    def get(self, key: K) -> Optional[V]:
        """Live value for ``key`` (``None`` if absent or tombstoned)."""
        entry = self._entries.get(key)
        return None if entry is None else entry[1]

    def entry(self, key: K) -> Optional[Tuple[Timestamp, Optional[V]]]:
        return self._entries.get(key)

    def live_items(self) -> Iterator[Tuple[K, V]]:
        for key, (_, value) in self._entries.items():
            if value is not None:
                yield key, value

    def keys(self) -> Iterator[K]:
        return iter(self._entries)

    def items(self) -> Iterator[Tuple[K, Tuple[Timestamp, Optional[V]]]]:
        """All entries, tombstones included."""
        return iter(self._entries.items())

    def max_timestamp(self) -> int:
        return max((ts[0] for ts, _ in self._entries.values()), default=0)

    def __len__(self) -> int:
        return sum(1 for _ in self.live_items())

    # -- writes ------------------------------------------------------------
    def set(self, key: K, ts: Timestamp, value: Optional[V]) -> bool:
        """Write ``value`` at ``ts``.  Returns True if the entry changed.

        A write only takes effect if ``ts`` is strictly greater than the stored
        timestamp, so applying the same write twice is a no-op.
        """
        current = self._entries.get(key)
        if current is not None and current[0] >= ts:
            return False
        self._entries[key] = (ts, value)
        return True

    def merge(self, other: "LWWMap[K, V]") -> Set[K]:
        """Pointwise-max join.  Returns the keys whose entry changed."""
        changed: Set[K] = set()
        for key, (ts, value) in other._entries.items():
            if self.set(key, ts, value):
                changed.add(key)
        return changed

    # -- misc --------------------------------------------------------------
    def copy(self) -> "LWWMap[K, V]":
        new: LWWMap[K, V] = LWWMap()
        new._entries = dict(self._entries)
        return new

    def to_dict(self) -> Dict[Any, Any]:
        return {k: (ts, v) for k, (ts, v) in self._entries.items()}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, LWWMap) and self._entries == other._entries

    def __repr__(self) -> str:
        return f"LWWMap({self._entries!r})"


class ORSet(Generic[E]):
    """Observed-remove set (add-wins).

    * ``_adds``: tag -> element, for every add ever observed.
    * ``_removed``: tags that have been tombstoned.
    * ``_live``: element -> set of its non-tombstoned tags (derived index,
      maintained incrementally so membership queries are O(1)).
    """

    def __init__(self) -> None:
        self._adds: Dict[Tag, E] = {}
        self._removed: Set[Tag] = set()
        self._live: Dict[E, Set[Tag]] = {}

    # -- reads -------------------------------------------------------------
    def contains(self, elem: E) -> bool:
        return elem in self._live

    __contains__ = contains

    def elements(self) -> Set[E]:
        return set(self._live)

    def tags_of(self, elem: E) -> Set[Tag]:
        return set(self._live.get(elem, ()))

    def is_removed(self, tag: Tag) -> bool:
        return tag in self._removed

    def add_items(self) -> Iterator[Tuple[Tag, E]]:
        return iter(self._adds.items())

    def removed_tags(self) -> Iterator[Tag]:
        return iter(self._removed)

    def max_seq(self) -> int:
        seqs = [tag[1] for tag in self._adds] + [tag[1] for tag in self._removed]
        return max(seqs, default=0)

    # -- writes ------------------------------------------------------------
    def add(self, tag: Tag, elem: E) -> bool:
        """Record an add with a fresh unique ``tag``.  Returns True if new."""
        if tag in self._adds:
            return False
        self._adds[tag] = elem
        if tag not in self._removed:
            self._live.setdefault(elem, set()).add(tag)
        return True

    def remove_tags(self, tags: Set[Tag]) -> Set[E]:
        """Tombstone ``tags``.  Returns elements whose liveness may have changed."""
        touched: Set[E] = set()
        for tag in tags:
            if tag in self._removed:
                continue
            self._removed.add(tag)
            elem = self._adds.get(tag)
            if elem is not None:
                live = self._live.get(elem)
                if live is not None:
                    live.discard(tag)
                    if not live:
                        del self._live[elem]
                touched.add(elem)
        return touched

    def merge(self, other: "ORSet[E]") -> Set[E]:
        """Union join.  Returns elements whose tag set changed."""
        changed: Set[E] = set()
        for tag, elem in other._adds.items():
            if self.add(tag, elem):
                changed.add(elem)
        changed |= self.remove_tags(other._removed)
        return changed

    # -- misc --------------------------------------------------------------
    def copy(self) -> "ORSet[E]":
        new: ORSet[E] = ORSet()
        new._adds = dict(self._adds)
        new._removed = set(self._removed)
        new._live = {e: set(t) for e, t in self._live.items()}
        return new

    def to_dict(self) -> Dict[str, Any]:
        return {"adds": dict(self._adds), "removed": set(self._removed)}

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ORSet)
            and self._adds == other._adds
            and self._removed == other._removed
        )

    def __repr__(self) -> str:
        return f"ORSet(live={sorted(map(repr, self._live))})"
