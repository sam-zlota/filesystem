"""Block index and matching work queue for a set of owned shards.

A :class:`BlockMatcher` holds the records that have at least one blocking
key in its shards, indexes them by key, queues (key, record) work when a
record arrives or a shard is gained, and turns queued work into match facts.

It runs either inside a :class:`~crdt_er.dist.node.Node` (one process does
everything) or inside a matcher worker process (``matchworker.py``), where a
node's main process forwards records to several workers that each own a
slice of the node's shards.  Facts carry the record versions they were
computed from, so a worker may lag behind the main process without harm: a
fact about a superseded version is simply dropped as stale.
"""
from __future__ import annotations

import time
import zlib
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from ..matching import Matcher, Normalized
from .state import Fact, Timestamp, make_fact


class BlockMatcher:
    def __init__(self, matcher: Matcher, n_shards: int, owned: Iterable[int] = ()) -> None:
        self.matcher = matcher
        self.n_shards = n_shards
        self.owned: Set[int] = set(owned)
        self.norm: Dict[str, Normalized] = {}
        self.ver: Dict[str, Timestamp] = {}
        self.block: Dict[str, Set[str]] = {}
        self.pending: Dict[str, Set[str]] = {}  # blocking key -> records to (re)match in it
        self._shard_cache: Dict[str, int] = {}
        self.metrics: Dict[str, int] = defaultdict(int)

    def shard_of(self, key: str) -> int:
        s = self._shard_cache.get(key)
        if s is None:
            s = self._shard_cache[key] = zlib.crc32(key.encode()) % self.n_shards
        return s

    # -- updates -------------------------------------------------------------
    def remove(self, rid: str) -> None:
        old = self.norm.pop(rid, None)
        self.ver.pop(rid, None)
        if old is None:
            return
        for k in old.keys:
            bucket = self.block.get(k)
            if bucket is not None:
                bucket.discard(rid)
                if not bucket:
                    del self.block[k]

    def upsert(self, rid: str, ver: Timestamp, nn: Normalized) -> None:
        """A record's current live version.  Queues it in every owned block."""
        self.remove(rid)
        keys = [k for k in nn.keys if self.shard_of(k) in self.owned]
        if not keys:
            return
        self.norm[rid] = nn
        self.ver[rid] = ver
        for k in keys:
            self.block.setdefault(k, set()).add(rid)
            self.pending.setdefault(k, set()).add(rid)

    def reset(self, owned: Iterable[int], records: Iterable[Tuple[str, Timestamp, Normalized]]) -> None:
        """Install a new owned shard set together with every live record.
        Blocks of newly gained shards are queued for a full rescan."""
        owned = set(owned)
        gained = owned - self.owned
        self.owned = owned
        self.norm, self.ver, self.block = {}, {}, {}
        for rid, ver, nn in records:
            keys = [k for k in nn.keys if self.shard_of(k) in owned]
            if not keys:
                continue
            self.norm[rid] = nn
            self.ver[rid] = ver
            for k in keys:
                self.block.setdefault(k, set()).add(rid)
                if self.shard_of(k) in gained:
                    self.pending.setdefault(k, set()).add(rid)
        for k in [k for k in self.pending if self.shard_of(k) not in owned]:
            del self.pending[k]

    # -- work ------------------------------------------------------------------
    def has_pending(self) -> bool:
        return bool(self.pending)

    def pending_count(self) -> int:
        return sum(len(q) for q in self.pending.values())

    def step(
        self,
        budget_s: Optional[float] = None,
        max_records: Optional[int] = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> Set[Fact]:
        """Score queued records against their blocks, one block at a time,
        until about ``max_records`` queued records or ``budget_s`` seconds have
        been processed.  Returns the match facts found."""
        deadline = None if budget_s is None else clock() + budget_s
        done = 0
        scored = 0
        found: Set[Fact] = set()
        norm, block, pending, owned, ver = self.norm, self.block, self.pending, self.owned, self.ver
        matcher = self.matcher
        match_block = getattr(matcher, "match_block", None)
        while pending:
            if max_records is not None and done >= max_records:
                break
            if deadline is not None and done and clock() >= deadline:
                break
            k, queued = pending.popitem()
            if self.shard_of(k) not in owned:
                continue
            members = block.get(k)
            if not members:
                continue
            queries = [q for q in queued if q in members]  # drop records that left the block
            if not queries:
                continue
            others = list(members)
            done += len(queries)
            scored += len(queries) * len(others)
            qn = [norm[q] for q in queries]
            mn = [norm[c] for c in others]
            if match_block is not None:
                pairs = match_block(k, qn, mn)
            else:
                pairs = [(i, j) for i, a in enumerate(qn) for j, b in enumerate(mn) if matcher.match(a, b)]
            for i, j in pairs:
                q, c = queries[i], others[j]
                if q == c:
                    continue
                if min(qn[i].keys & mn[j].keys) != k:
                    continue  # accepted only under the pair's smallest shared key
                found.add(make_fact(q, ver[q], c, ver[c]))
        self.metrics["records_matched"] += done
        self.metrics["pairs_scored"] += scored
        return found

    def check(self) -> None:
        for k, bucket in self.block.items():
            assert self.shard_of(k) in self.owned and bucket
            for rid in bucket:
                assert k in self.norm[rid].keys
