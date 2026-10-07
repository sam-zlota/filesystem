"""Deterministic in-process cluster simulator for :class:`Node`.

Models what the asyncio server does, minus sockets and wall-clock time:

* broadcast of every originated delta, with random loss, duplication and
  reordering;
* per-node durable logs ("disks"), with crashes that can lose the unsynced
  tail of the log, and restarts with a new incarnation that replay the log;
* network partitions, and membership views that are updated lazily and
  independently per node (so nodes can disagree about who owns a shard);
* anti-entropy by version-vector exchange.

``quiesce()`` heals everything and runs the system until no node has any
work left, which is the point at which the CRDT guarantees apply.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..matching import Matcher, PersonMatcher
from .node import Delta, Node


class SimCluster:
    def __init__(
        self,
        n: int,
        rng: random.Random,
        matcher: Optional[Matcher] = None,
        n_shards: int = 16,
        compute_replicas: int = 1,
        loss: float = 0.05,
        dup: float = 0.2,
    ) -> None:
        self.rng = rng
        self.ids = [f"n{i}" for i in range(n)]
        self.matcher = matcher or PersonMatcher()
        self.n_shards = n_shards
        self.compute_replicas = compute_replicas
        self.loss = loss
        self.dup = dup
        self.incarnation = {i: 1 for i in self.ids}
        self.nodes: Dict[str, Node] = {i: self._new_node(i) for i in self.ids}
        self.disk: Dict[str, List[Delta]] = {i: [] for i in self.ids}
        self.up: Set[str] = set(self.ids)
        self.group: Dict[str, int] = {i: 0 for i in self.ids}
        self.queues: Dict[str, List[Tuple[str, Delta]]] = {i: [] for i in self.ids}

    def _new_node(self, nid: str) -> Node:
        return Node(nid, self.incarnation[nid], self.ids, self.matcher, self.n_shards, self.compute_replicas)

    # -- topology ------------------------------------------------------------
    def reachable(self, a: str, b: str) -> bool:
        return a in self.up and b in self.up and self.group[a] == self.group[b]

    def partition(self, groups: Sequence[Sequence[str]]) -> None:
        for g, members in enumerate(groups):
            for m in members:
                self.group[m] = g

    def heal(self) -> None:
        self.group = {i: 0 for i in self.ids}

    def refresh_membership(self, nid: Optional[str] = None) -> None:
        """Perfect failure detector, applied to one node (or all)."""
        targets = [nid] if nid else list(self.up)
        for t in targets:
            if t in self.up:
                self.nodes[t].set_alive({m for m in self.ids if self.reachable(t, m)})

    def crash(self, nid: str, lose_tail: int = 0) -> None:
        self.up.discard(nid)
        self.queues[nid].clear()
        if lose_tail:
            del self.disk[nid][-lose_tail:]

    def restart(self, nid: str) -> None:
        self.incarnation[nid] += 1
        node = self._new_node(nid)
        for origin, n, wire in self.disk[nid]:
            node.receive(origin, n, wire)
        self.nodes[nid] = node
        self.up.add(nid)

    # -- traffic ---------------------------------------------------------------
    def _accept(self, nid: str, delta: Delta) -> None:
        self.disk[nid].append(delta)

    def broadcast(self, src: str, delta: Delta, lossy: bool = True) -> None:
        for d in self.ids:
            if d == src or not self.reachable(src, d):
                continue
            if lossy and self.rng.random() < self.loss:
                continue
            self.queues[d].append((src, delta))

    def submit(self, nid: str, ops: Sequence[Sequence[Any]]) -> List[Any]:
        results, delta = self.nodes[nid].submit(ops)
        if delta is not None:
            self._accept(nid, delta)
            self.broadcast(nid, delta)
        return results

    def deliver_one(self, lossy: bool = True) -> bool:
        busy = [d for d, q in self.queues.items() if q]
        if not busy:
            return False
        dest = self.rng.choice(busy)
        q = self.queues[dest]
        i = self.rng.randrange(len(q))
        src, delta = q[i]
        if not (lossy and self.rng.random() < self.dup):
            q.pop(i)
        if self.reachable(src, dest) and self.nodes[dest].receive(*delta):
            self._accept(dest, delta)
        return True

    def match_step(self, nid: str, max_records: int = 5, lossy: bool = True) -> bool:
        if nid not in self.up or not self.nodes[nid].has_pending():
            return False
        delta = self.nodes[nid].step_matching(max_records=max_records)
        if delta is not None:
            self._accept(nid, delta)
            self.broadcast(nid, delta, lossy)
        return True

    def anti_entropy(self, a: str, b: str) -> int:
        """``a`` pulls from ``b`` whatever its version vector lacks."""
        if not self.reachable(a, b):
            return 0
        missing = self.nodes[b].missing_for(self.nodes[a].vv_summary())
        for delta in missing:
            if self.nodes[a].receive(*delta):
                self._accept(a, delta)
        return len(missing)

    # -- convergence -----------------------------------------------------------
    def quiesce(self) -> None:
        for nid in self.ids:
            if nid not in self.up:
                self.restart(nid)
        self.heal()
        self.refresh_membership()
        while True:
            progress = False
            while self.deliver_one(lossy=False):
                progress = True
            for a in self.ids:
                for b in self.ids:
                    if a != b and self.anti_entropy(a, b):
                        progress = True
            for nid in self.ids:
                while self.match_step(nid, max_records=50, lossy=False):
                    progress = True
            if not progress:
                return
