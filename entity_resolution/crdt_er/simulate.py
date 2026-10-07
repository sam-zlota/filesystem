"""An unreliable network simulator for exercising replicas.

Deltas are queued per destination and delivered in random order, possibly
more than once.  ``anti_entropy`` performs full-state pairwise syncs, which
is how a real deployment would recover from *lost* deltas (delta loss is the
one failure a delta-only protocol cannot absorb on its own).
"""
from __future__ import annotations

import random
from typing import Dict, Iterable, List

from .engine import Replica
from .state import ERState


class Network:
    def __init__(self, replicas: Iterable[Replica], rng: random.Random, duplicate_prob: float = 0.0) -> None:
        self.replicas: Dict[str, Replica] = {r.id: r for r in replicas}
        self.rng = rng
        self.duplicate_prob = duplicate_prob
        self.queues: Dict[str, List[ERState]] = {rid: [] for rid in self.replicas}
        self.delivered = 0

    def broadcast(self, src: str, delta: ERState) -> None:
        for dest in self.replicas:
            if dest != src:
                self.queues[dest].append(delta)

    def pending(self) -> int:
        return sum(len(q) for q in self.queues.values())

    def deliver_one(self) -> bool:
        """Deliver one random queued delta (random order; maybe duplicated)."""
        busy = [d for d, q in self.queues.items() if q]
        if not busy:
            return False
        dest = self.rng.choice(busy)
        queue = self.queues[dest]
        i = self.rng.randrange(len(queue))
        delta = queue[i]
        if self.rng.random() >= self.duplicate_prob:
            queue.pop(i)  # otherwise leave it queued: it will be delivered again
        self.replicas[dest].merge(delta)
        self.delivered += 1
        return True

    def flush(self) -> None:
        saved = self.duplicate_prob
        self.duplicate_prob = 0.0
        try:
            while self.deliver_one():
                pass
        finally:
            self.duplicate_prob = saved

    def anti_entropy(self, rounds: int = 2) -> None:
        """Full-state gossip: every replica merges every other's snapshot."""
        ids = list(self.replicas)
        for _ in range(rounds):
            for a in ids:
                for b in ids:
                    if a != b:
                        self.replicas[a].merge(self.replicas[b].snapshot())
