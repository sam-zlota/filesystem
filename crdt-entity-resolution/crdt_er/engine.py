"""Incremental resolver: maintains the derived entity view under deltas.

The resolver is *not* itself replicated. It is a materialized view over the
replicated state (records + must/cannot-link evidence). The contract, checked
by the property tests, is:

    resolver after any sequence of deltas  ==  IncrementalResolver.batch(state)

i.e. incremental maintenance is indistinguishable from recomputing from
scratch, so two replicas with equal CRDT state always expose equal entities.

Locality: the clustering of a connected component of the candidate graph G
(match edges with score >= threshold, plus live must-links) depends only on
that component. A delta can therefore only change clusters inside components
that contain a "dirty" record, and only those are recomputed.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Iterable, Mapping, Optional

from .blocking import Blocker
from .clustering import Pair, constrained_greedy
from .config import DEFAULT_CONFIG, ResolverConfig
from .similarity import score

Record = Mapping[str, str]


def canonical_pair(a: str, b: str) -> Pair:
    if a == b:
        raise ValueError("a pair needs two distinct record ids")
    return (a, b) if a < b else (b, a)


class IncrementalResolver:
    def __init__(self, config: ResolverConfig = DEFAULT_CONFIG):
        self.config = config
        self.blocker = Blocker(config)
        self.records: dict[str, dict[str, str]] = {}
        # Candidate graph G, scored part: rid -> {neighbor: score >= threshold}
        self.match_adj: dict[str, dict[str, float]] = defaultdict(dict)
        # Evidence. Stored even when an endpoint is absent (it may arrive later).
        self.must: set[Pair] = set()
        self.cannot: set[Pair] = set()
        self.must_adj: dict[str, set[str]] = defaultdict(set)
        self.cannot_adj: dict[str, set[str]] = defaultdict(set)
        # Derived view. Cluster id = smallest member id (deterministic).
        self.cluster_of: dict[str, str] = {}
        self.clusters: dict[str, frozenset[str]] = {}
        self.stats: Counter[str] = Counter()

    # ------------------------------------------------------------------ deltas

    def apply(
        self,
        records: Mapping[str, Optional[Record]] = {},
        links: Mapping[Pair, tuple[bool, bool]] = {},
    ) -> set[str]:
        """Apply a delta and re-cluster what it affects.

        ``records`` maps record id -> current fields (None = deleted).
        ``links`` maps canonical pair -> (has_must_link, has_cannot_link).
        Values are *current state*, not operations, so applying the same delta
        twice is harmless (idempotent) and deltas may be coalesced.
        Returns the ids of live records whose cluster was recomputed.
        """
        dirty: set[str] = set()
        for rid in sorted(records):
            self._apply_record(rid, records[rid], dirty)
        for pair in sorted(links):
            self._apply_link(pair, *links[pair], dirty)
        return self._recluster(dirty)

    def _apply_record(self, rid: str, rec: Optional[Record], dirty: set[str]) -> None:
        old = self.records.get(rid)
        new = dict(rec) if rec is not None else None
        if old == new:
            return
        self.stats["record_deltas"] += 1
        dirty.add(rid)
        dirty.update(self.must_adj.get(rid, ()))  # their G-adjacency changes
        for n in self.match_adj.pop(rid, {}):
            del self.match_adj[n][rid]
            dirty.add(n)
        if new is None:
            del self.records[rid]
            self.blocker.remove(rid)
            return
        self.records[rid] = new
        self.blocker.upsert(rid, new)
        for c in self.blocker.candidates(rid):
            a, b = canonical_pair(rid, c)
            s = score(self.records[a], self.records[b], self.config)
            self.stats["pairs_scored"] += 1
            if s >= self.config.match_threshold:
                self.match_adj[rid][c] = s
                self.match_adj[c][rid] = s
                dirty.add(c)

    def _apply_link(self, pair: Pair, has_must: bool, has_cannot: bool, dirty: set[str]) -> None:
        a, b = pair
        changed = False
        for flag, edges, adj in ((has_must, self.must, self.must_adj), (has_cannot, self.cannot, self.cannot_adj)):
            if flag and pair not in edges:
                edges.add(pair)
                adj[a].add(b)
                adj[b].add(a)
                changed = True
            elif not flag and pair in edges:
                edges.discard(pair)
                adj[a].discard(b)
                adj[b].discard(a)
                changed = True
        if changed:
            self.stats["link_deltas"] += 1
            dirty.update(pair)

    # -------------------------------------------------------------- reclustering

    def _neighbors(self, v: str) -> Iterable[str]:
        yield from self.match_adj.get(v, ())
        for n in self.must_adj.get(v, ()):
            if n in self.records:
                yield n

    def _component(self, start: str) -> set[str]:
        seen = {start}
        queue = deque([start])
        while queue:
            for n in self._neighbors(queue.popleft()):
                if n not in seen:
                    seen.add(n)
                    queue.append(n)
        return seen

    def _drop_cluster(self, cid: str, pending: set[str]) -> None:
        for m in self.clusters.pop(cid, ()):
            if self.cluster_of.get(m) == cid:
                del self.cluster_of[m]
            if m in self.records:
                pending.add(m)

    def _recluster(self, dirty: set[str]) -> set[str]:
        # Every member of an old cluster that touched the delta must be
        # re-placed: its old cluster may have split.
        pending: set[str] = set()
        for v in dirty:
            if v in self.cluster_of:
                self._drop_cluster(self.cluster_of[v], pending)
            elif v in self.records:
                pending.add(v)

        recomputed: set[str] = set()
        for v in sorted(pending):
            if v in recomputed:
                continue
            comp = self._component(v)
            self.stats["components_recomputed"] += 1
            self.stats["max_component"] = max(self.stats["max_component"], len(comp))
            # A merge may pull in clean nodes whose (intact) cluster must go too.
            for n in comp:
                if n in self.cluster_of:
                    self._drop_cluster(self.cluster_of[n], set())
            must_edges = [(u, n) for u in comp for n in self.must_adj.get(u, ()) if u < n and n in comp]
            scored = [(s, u, n) for u in comp for n, s in self.match_adj.get(u, {}).items() if u < n]
            for cluster in constrained_greedy(comp, must_edges, scored, self.cannot_adj):
                cid = min(cluster)
                self.clusters[cid] = cluster
                for m in cluster:
                    self.cluster_of[m] = cid
            recomputed |= comp
        return recomputed

    # ------------------------------------------------------------------ queries

    def snapshot(self) -> frozenset[frozenset[str]]:
        """Order-free representation of the partition, for equality checks."""
        return frozenset(self.clusters.values())

    def entity(self, cid: str) -> dict:
        """Golden record via deterministic survivorship: per field, the most
        frequent value among members; ties broken by smallest value."""
        members = sorted(self.clusters[cid])
        votes: dict[str, Counter[str]] = defaultdict(Counter)
        for m in members:
            for f, v in self.records[m].items():
                votes[f][v] += 1
        fields = {f: min(c.items(), key=lambda kv: (-kv[1], kv[0]))[0] for f, c in sorted(votes.items())}
        return {"id": cid, "members": members, "fields": fields}

    def entities(self) -> list[dict]:
        return [self.entity(cid) for cid in sorted(self.clusters)]

    def conflicts(self) -> list[Pair]:
        """Live must-links that the clustering could not honour because they
        contradict cannot-links (directly or transitively). Surfaced for
        human review rather than silently resolved."""
        return sorted(
            p for p in self.must
            if p[0] in self.records and p[1] in self.records
            and self.cluster_of[p[0]] != self.cluster_of[p[1]]
        )

    @classmethod
    def batch(
        cls,
        records: Mapping[str, Record],
        must: Iterable[Pair],
        cannot: Iterable[Pair],
        config: ResolverConfig = DEFAULT_CONFIG,
    ) -> "IncrementalResolver":
        """Reference (from-scratch) computation over a full state."""
        links: dict[Pair, list[bool]] = defaultdict(lambda: [False, False])
        for p in must:
            links[canonical_pair(*p)][0] = True
        for p in cannot:
            links[canonical_pair(*p)][1] = True
        r = cls(config)
        r.apply(dict(records), {p: (m, c) for p, (m, c) in links.items()})
        return r
