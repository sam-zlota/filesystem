"""Replicated state of a distributed node.

It is the single-node :class:`~crdt_er.state.ERState` lattice plus one more
component, the **match facts**.  A fact is an immutable statement

    "version ts_a of record a matches version ts_b of record b"

produced by whichever node owns the blocking shard the pair falls in.  Facts
are replicated so that no other node has to repeat the comparison.

Why this stays a CRDT
---------------------
* Facts only ever get added; a fact never changes meaning.
* A fact is *valid* when both versions it names are the current live versions.
  Record versions only move forward, so once a fact is *stale* (a newer version
  of either record exists) it can never become valid again.  Stale facts are
  therefore dropped on arrival and purged when a record changes.  Because
  staleness is monotone, "union, then drop stale" gives the same result in any
  merge order, so the join stays commutative, associative and idempotent.
* The matcher is deterministic, so two owners that evaluate the same pair at
  the same versions emit the identical fact.  Duplicate work is harmless.

Wire format
-----------
Deltas travel as plain msgpack-friendly dicts of lists::

    {"r": [[rid, lamport, replica, attrs_or_None], ...],   # record writes
     "a": [[replica, seq, kind, a, b], ...],               # assertion adds
     "x": [[replica, seq], ...],                           # assertion removes
     "f": [[a, lamport_a, replica_a, b, lamport_b, replica_b], ...]}  # facts
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from ..state import ERState

Timestamp = Tuple[int, str]
#: (a, lamport_a, replica_a, b, lamport_b, replica_b) with a < b
Fact = Tuple[str, int, str, str, int, str]
Wire = Dict[str, List[Any]]


def make_fact(a: str, ts_a: Timestamp, b: str, ts_b: Timestamp) -> Fact:
    if b < a:
        a, ts_a, b, ts_b = b, ts_b, a, ts_a
    return (a, ts_a[0], ts_a[1], b, ts_b[0], ts_b[1])


def combine(*wires: Wire) -> Wire:
    out: Wire = {}
    for w in wires:
        for k, v in w.items():
            if v:
                out.setdefault(k, []).extend(v)
    return out


@dataclass
class Changes:
    records: Set[str] = field(default_factory=set)
    pairs: Set[Tuple[str, str]] = field(default_factory=set)
    facts: Set[Fact] = field(default_factory=set)

    def touched(self) -> Set[str]:
        out = set(self.records)
        for a, b in self.pairs:
            out.add(a)
            out.add(b)
        for f in self.facts:
            out.add(f[0])
            out.add(f[3])
        return out

    def update(self, other: "Changes") -> None:
        self.records |= other.records
        self.pairs |= other.pairs
        self.facts |= other.facts

    def __bool__(self) -> bool:
        return bool(self.records or self.pairs or self.facts)


class DistState:
    def __init__(self) -> None:
        self.er = ERState()
        self.facts: Set[Fact] = set()
        self.facts_by_rid: Dict[str, Set[Fact]] = defaultdict(set)
        self.max_counter = 0

    # -- record versions ---------------------------------------------------
    def version(self, rid: str) -> Optional[Timestamp]:
        entry = self.er.records.entry(rid)
        return None if entry is None else entry[0]

    def live_version(self, rid: str) -> Optional[Timestamp]:
        entry = self.er.records.entry(rid)
        return None if entry is None or entry[1] is None else entry[0]

    def fact_is_stale(self, f: Fact) -> bool:
        entries = self.er.records.raw()
        e = entries.get(f[0])
        if e is not None and e[0] > (f[1], f[2]):
            return True
        e = entries.get(f[3])
        return e is not None and e[0] > (f[4], f[5])

    def fact_is_valid(self, f: Fact) -> bool:
        return (
            self.live_version(f[0]) == (f[1], f[2])
            and self.live_version(f[3]) == (f[4], f[5])
        )

    # -- join ----------------------------------------------------------------
    def merge(self, wire: Wire, facts_validated: bool = False) -> Changes:
        """Join ``wire`` into this state.  ``facts_validated`` skips the
        staleness check for facts the caller has just checked against the
        current versions (a node publishing its own matching results)."""
        ch = Changes()
        entries = self.er.records.raw()
        max_counter = self.max_counter
        for rid, lamport, rep, attrs in wire.get("r", ()):
            ts = (lamport, rep)
            cur = entries.get(rid)
            if cur is None or cur[0] < ts:  # same rule as LWWMap.set, inlined
                entries[rid] = (ts, attrs)
                ch.records.add(rid)
            if lamport > max_counter:
                max_counter = lamport
        self.max_counter = max_counter
        assertions = self.er.assertions
        for rep, seq, kind, a, b in wire.get("a", ()):
            if assertions.add((rep, seq), (kind, a, b)):
                ch.pairs.add((a, b))
            if seq > self.max_counter:
                self.max_counter = seq
        for rep, seq in wire.get("x", ()):
            tag = (rep, seq)
            if not assertions.is_removed(tag):
                for _, a, b in assertions.remove_tags({tag}):
                    ch.pairs.add((a, b))
            if seq > self.max_counter:
                self.max_counter = seq
        for raw in wire.get("f", ()):
            f: Fact = tuple(raw)  # type: ignore[assignment]
            if f in self.facts or (not facts_validated and self.fact_is_stale(f)):
                continue
            self.facts.add(f)
            self.facts_by_rid[f[0]].add(f)
            self.facts_by_rid[f[3]].add(f)
            ch.facts.add(f)
        for rid in ch.records:  # a new record version makes its old facts stale
            bucket = self.facts_by_rid.get(rid)
            if not bucket:
                continue
            for f in [f for f in bucket if self.fact_is_stale(f)]:
                self.facts.discard(f)
                self.facts_by_rid[f[0]].discard(f)
                self.facts_by_rid[f[3]].discard(f)
        return ch

    # -- export --------------------------------------------------------------
    def full_wire(self) -> Wire:
        return {
            "r": [[rid, ts[0], ts[1], attrs] for rid, (ts, attrs) in self.er.records.items()],
            "a": [[t[0], t[1], k, a, b] for t, (k, a, b) in self.er.assertions.add_items()],
            "x": [[t[0], t[1]] for t in self.er.assertions.removed_tags()],
            "f": [list(f) for f in self.facts],
        }

    def canonical(self) -> Tuple[Any, ...]:
        """Order-independent fingerprint, for equality checks in tests."""
        w = self.full_wire()
        return tuple(
            tuple(sorted(repr(e) for e in w[k])) for k in ("r", "a", "x", "f")
        )
