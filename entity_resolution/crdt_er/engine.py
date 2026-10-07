"""Replica: local operations, delta replication and incremental resolution.

A :class:`Replica` owns one :class:`~crdt_er.state.ERState` plus *derived*
indexes that are never replicated:

* ``_norm``      record id -> Normalized (live records only)
* ``_index``     blocking key -> set of live record ids
* ``_partners``  record id -> records it shares a live assertion with
* ``_adj``       match graph over live records (symmetric)
* ``_entity_of`` record id -> entity id;  ``_members`` entity id -> records

Every change, local or remote, arrives as a delta ``ERState`` through
:meth:`Replica.merge`.  The merge reports which records / pairs changed, and
:meth:`Replica._refresh` repairs the derived indexes *only around those
records*: it re-evaluates the edges incident to them and re-derives
connectivity for the handful of clusters they touch.  Nothing else is
recomputed, which is what makes resolution incremental.

The entity clustering is a pure function of the state: connected components
of the graph whose edges are ``decide(a, b)`` for live records ``a, b``.  An
entity's id is the smallest record id in its component, so entity ids are
deterministic across replicas too.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from .lattice import Tag, Timestamp
from .matching import Matcher, Normalized, PersonMatcher
from .state import LINK, UNLINK, Assertion, Attrs, Dirty, ERState, Pair, RecordId, pair_key

EntityId = str


def decide(state: ERState, matcher: Matcher, a: RecordId, na: Normalized, b: RecordId, nb: Normalized) -> bool:
    """The single source of truth for "is there an edge between a and b".

    Precedence: a live *unlink* assertion always wins (a curator said these are
    different people), then a live *link* assertion, then the automatic
    matcher.  Symmetric by construction.
    """
    p = pair_key(a, b)
    if state.has_assertion(UNLINK, *p):
        return False
    if state.has_assertion(LINK, *p):
        return True
    return matcher.match(na, nb)


class Replica:
    def __init__(self, replica_id: str, matcher: Optional[Matcher] = None) -> None:
        self.id = replica_id
        self.matcher: Matcher = matcher if matcher is not None else PersonMatcher()
        self.state = ERState()
        self._counter = 0  # Lamport counter; also the source of unique tags
        self._next_local_id = 0

        self._norm: Dict[RecordId, Normalized] = {}
        self._index: Dict[str, Set[RecordId]] = {}
        self._partners: Dict[RecordId, Set[RecordId]] = defaultdict(set)
        self._adj: Dict[RecordId, Set[RecordId]] = {}
        self._entity_of: Dict[RecordId, EntityId] = {}
        self._members: Dict[EntityId, Set[RecordId]] = {}

    # ------------------------------------------------------------------
    # clocks / ids
    # ------------------------------------------------------------------
    def _tick(self) -> int:
        self._counter += 1
        return self._counter

    def _ts(self) -> Timestamp:
        return (self._tick(), self.id)

    def _tag(self) -> Tag:
        return (self.id, self._tick())

    def _fresh_record_id(self) -> RecordId:
        while True:
            self._next_local_id += 1
            rid = f"{self.id}/{self._next_local_id:06d}"
            if self.state.records.entry(rid) is None:
                return rid

    # ------------------------------------------------------------------
    # local operations -- each returns the delta to broadcast
    # ------------------------------------------------------------------
    def add_record(self, attrs: Attrs, record_id: Optional[RecordId] = None) -> Tuple[RecordId, ERState]:
        """Insert (or, for an existing id, overwrite) a record."""
        rid = record_id if record_id is not None else self._fresh_record_id()
        delta = ERState.delta_record(rid, self._ts(), dict(attrs))
        self.merge(delta)
        return rid, delta

    def update_record(self, rid: RecordId, attrs: Attrs) -> ERState:
        """Replace a live record's attributes (whole-record last-writer-wins)."""
        if not self.state.is_live(rid):
            raise KeyError(f"{rid!r} is not a live record on replica {self.id}")
        delta = ERState.delta_record(rid, self._ts(), dict(attrs))
        self.merge(delta)
        return delta

    def patch_record(self, rid: RecordId, **changes: Any) -> ERState:
        current = self.state.record(rid)
        if current is None:
            raise KeyError(f"{rid!r} is not a live record on replica {self.id}")
        merged = dict(current)
        merged.update(changes)
        return self.update_record(rid, merged)

    def delete_record(self, rid: RecordId) -> ERState:
        delta = ERState.delta_record(rid, self._ts(), None)
        self.merge(delta)
        return delta

    def link(self, a: RecordId, b: RecordId) -> ERState:
        """Curator assertion: a and b are the same entity."""
        return self._assert((LINK,) + pair_key(a, b))

    def unlink(self, a: RecordId, b: RecordId) -> ERState:
        """Curator assertion: a and b are NOT the same entity (overrides matcher and link)."""
        return self._assert((UNLINK,) + pair_key(a, b))

    def retract_link(self, a: RecordId, b: RecordId) -> ERState:
        return self._retract((LINK,) + pair_key(a, b))

    def retract_unlink(self, a: RecordId, b: RecordId) -> ERState:
        return self._retract((UNLINK,) + pair_key(a, b))

    def _assert(self, assertion: Assertion) -> ERState:
        delta = ERState.delta_assert(self._tag(), assertion)
        self.merge(delta)
        return delta

    def _retract(self, assertion: Assertion) -> ERState:
        observed = self.state.assertions.tags_of(assertion)
        delta = ERState.delta_retract(observed)  # empty delta if nothing observed
        self.merge(delta)
        return delta

    # ------------------------------------------------------------------
    # replication
    # ------------------------------------------------------------------
    def merge(self, incoming: ERState) -> Dirty:
        """Join a delta or a full state into this replica, then repair views."""
        dirty = self.state.merge(incoming)
        self._counter = max(self._counter, incoming.max_counter())
        if dirty:
            self._refresh(dirty)
        return dirty

    def snapshot(self) -> ERState:
        """Copy of the full state, for anti-entropy / state-based sync."""
        return self.state.copy()

    # ------------------------------------------------------------------
    # incremental maintenance
    # ------------------------------------------------------------------
    def _refresh(self, dirty: Dirty) -> None:
        # Assertion changes may alter which records are "partners" of which.
        for a, b in dirty.pairs:
            if self._has_any_assertion(a, b):
                self._partners[a].add(b)
                self._partners[b].add(a)
            else:
                self._partners[a].discard(b)
                self._partners[b].discard(a)

        touched = dirty.touched_records()
        affected: Set[EntityId] = set()

        # 1. Detach every touched record from the derived indexes.  Its old
        #    neighbours live in the same entity, so marking that entity as
        #    affected is enough to re-derive their connectivity later.
        for r in touched:
            eid = self._entity_of.get(r)
            if eid is not None:
                affected.add(eid)
            for nb in self._adj.pop(r, set()):
                self._adj[nb].discard(r)
            norm = self._norm.pop(r, None)
            if norm is not None:
                for key in norm.keys:
                    bucket = self._index[key]
                    bucket.discard(r)
                    if not bucket:
                        del self._index[key]

        # 2. Re-attach the ones that are (still) live with fresh attributes.
        live_touched: List[RecordId] = []
        for r in touched:
            attrs = self.state.record(r)
            if attrs is None:
                continue
            norm = self.matcher.normalize(attrs)
            self._norm[r] = norm
            for key in norm.keys:
                self._index.setdefault(key, set()).add(r)
            self._adj[r] = set()
            live_touched.append(r)

        # 3. Re-evaluate only the edges incident to touched records.
        for r in live_touched:
            for c in self._candidates(r):
                if decide(self.state, self.matcher, r, self._norm[r], c, self._norm[c]):
                    self._adj[r].add(c)
                    self._adj[c].add(r)
                    eid = self._entity_of.get(c)
                    if eid is not None:
                        affected.add(eid)

        # 4. Re-derive connectivity for exactly the affected clusters.
        nodes: Set[RecordId] = set(live_touched)
        for eid in affected:
            nodes |= self._members.pop(eid, set())
        for r in nodes:
            self._entity_of.pop(r, None)
        nodes = {r for r in nodes if r in self._norm}  # drop deleted records
        self._assign_components(nodes)

    def _candidates(self, r: RecordId) -> Set[RecordId]:
        cands: Set[RecordId] = set()
        for key in self._norm[r].keys:
            cands |= self._index.get(key, set())
        cands |= {p for p in self._partners.get(r, ()) if p in self._norm}
        cands.discard(r)
        return cands

    def _has_any_assertion(self, a: RecordId, b: RecordId) -> bool:
        return self.state.has_assertion(LINK, a, b) or self.state.has_assertion(UNLINK, a, b)

    def _assign_components(self, nodes: Set[RecordId]) -> None:
        unvisited = set(nodes)
        while unvisited:
            start = unvisited.pop()
            component = {start}
            stack = [start]
            while stack:
                x = stack.pop()
                for nb in self._adj[x]:
                    if nb not in component:
                        # Invariant: an edge never leaves the affected node set.
                        assert nb in nodes, (x, nb)
                        component.add(nb)
                        unvisited.discard(nb)
                        stack.append(nb)
            eid = min(component)
            self._members[eid] = component
            for r in component:
                self._entity_of[r] = eid

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def entity_of(self, rid: RecordId) -> Optional[EntityId]:
        return self._entity_of.get(rid)

    def members(self, eid: EntityId) -> FrozenSet[RecordId]:
        return frozenset(self._members.get(eid, ()))

    def entities(self) -> Dict[EntityId, FrozenSet[RecordId]]:
        return {eid: frozenset(m) for eid, m in self._members.items()}

    def records(self) -> Dict[RecordId, Dict[str, Any]]:
        return {rid: dict(attrs) for rid, attrs in self.state.records.live_items()}

    def neighbours(self, rid: RecordId) -> FrozenSet[RecordId]:
        return frozenset(self._adj.get(rid, ()))

    def explain(self, a: RecordId, b: RecordId) -> str:
        if a not in self._norm or b not in self._norm:
            return "at least one record is not live"
        p = pair_key(a, b)
        if self.state.has_assertion(UNLINK, *p):
            return "no edge: unlink assertion"
        if self.state.has_assertion(LINK, *p):
            return "edge: link assertion"
        matched = self.matcher.match(self._norm[a], self._norm[b])
        return "edge: automatic match" if matched else "no edge: matcher rejected"

    # ------------------------------------------------------------------
    # self-checks
    # ------------------------------------------------------------------
    def check_invariants(self) -> None:
        """Verify derived views against a from-scratch resolution."""
        expected = resolve_from_scratch(self.state, self.matcher)
        actual = self.entities()
        assert actual == expected, f"incremental view diverged on {self.id}:\n {actual}\n!= {expected}"
        live = self.state.live_record_ids()
        assert set(self._norm) == live
        assert set(self._adj) == live
        assert set(self._entity_of) == live
        for r, nbs in self._adj.items():
            for nb in nbs:
                assert r in self._adj[nb], "adjacency not symmetric"
                assert self._entity_of[r] == self._entity_of[nb], "edge crosses entities"
        for key, bucket in self._index.items():
            assert bucket, "empty index bucket retained"
            for r in bucket:
                assert key in self._norm[r].keys


# ----------------------------------------------------------------------
# Batch oracle (independent of the incremental bookkeeping)
# ----------------------------------------------------------------------
def resolve_from_scratch(state: ERState, matcher: Matcher) -> Dict[EntityId, FrozenSet[RecordId]]:
    """Full recomputation of the clustering from a state.  Used as the test
    oracle for the incremental engine and as a reference definition of what
    "the entities of a state" means."""
    live: Dict[RecordId, Normalized] = {
        rid: matcher.normalize(attrs) for rid, attrs in state.records.live_items()
    }
    index: Dict[str, Set[RecordId]] = defaultdict(set)
    for rid, norm in live.items():
        for key in norm.keys:
            index[key].add(rid)

    pairs: Set[Pair] = set()
    for bucket in index.values():
        ordered = sorted(bucket)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pairs.add((a, b))
    for _, a, b in state.assertions.elements():
        if a in live and b in live:
            pairs.add((a, b))

    adj: Dict[RecordId, Set[RecordId]] = {rid: set() for rid in live}
    for a, b in pairs:
        if decide(state, matcher, a, live[a], b, live[b]):
            adj[a].add(b)
            adj[b].add(a)

    out: Dict[EntityId, FrozenSet[RecordId]] = {}
    seen: Set[RecordId] = set()
    for start in live:
        if start in seen:
            continue
        component = {start}
        stack = [start]
        while stack:
            x = stack.pop()
            for nb in adj[x]:
                if nb not in component:
                    component.add(nb)
                    stack.append(nb)
        seen |= component
        out[min(component)] = frozenset(component)
    return out
