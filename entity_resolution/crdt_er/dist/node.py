"""Transport-agnostic logic of one cluster node.

The same class runs inside the asyncio server (``server.py``) and inside the
deterministic in-process simulator used by the tests (``sim.py``).  It never
does I/O; callers move the deltas it returns.

Responsibilities
----------------
1. **Replica of the shared state** (:class:`DistState`).  Every node holds all
   records, assertions and facts.  Applying a record is cheap (a dict write,
   a normalisation and a local view repair); it is the *matching* that costs.
2. **Delta delivery bookkeeping.**  Every delta a node originates gets an id
   ``(origin replica, seq)``.  Nodes track which ids they hold (a version
   vector plus out-of-order extras), so duplicates are dropped before any
   work, and anti-entropy can compute exactly which deltas a peer lacks.
3. **Sharded matching.**  Blocking keys hash into ``n_shards`` shards.  Each
   shard is owned by the top ``compute_replicas`` live members under
   rendezvous hashing.  A node only compares records inside blocks it owns,
   and a pair sharing several keys is compared only under its smallest shared
   key.  Results are published as facts.  When membership changes, shards
   move and the new owner rescans them.
4. **Incremental entity view.**  Edges are ``unlink? no : link? yes : valid
   fact?``, the same rule as the single-node engine, so at quiescence the view
   equals :func:`crdt_er.engine.resolve_from_scratch` on the same state.

Incarnations
------------
A node's replica id is ``"<node>#<incarnation>"``.  The incarnation is bumped
durably on every start, so a node that lost the tail of its log can never
reissue a timestamp or tag that a peer already holds for a different write.
"""
from __future__ import annotations

import hashlib
import math
import time
import zlib
from collections import defaultdict
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from ..matching import Matcher, Normalized, PersonMatcher
from ..state import LINK, UNLINK, pair_key
from .state import Changes, DistState, Fact, Timestamp, Wire, combine, make_fact

Delta = Tuple[str, int, Wire]  # (origin replica, seq, wire)


def _rank(shard: int, member: str) -> int:
    digest = hashlib.blake2b(f"{shard}|{member}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


class Node:
    def __init__(
        self,
        node_id: str,
        incarnation: int,
        members: Iterable[str],
        matcher: Optional[Matcher] = None,
        n_shards: int = 64,
        compute_replicas: int = 1,
    ) -> None:
        self.id = node_id
        self.incarnation = incarnation
        self.replica = f"{node_id}#{incarnation}"
        self.members: Tuple[str, ...] = tuple(sorted(set(members) | {node_id}))
        self.matcher: Matcher = matcher if matcher is not None else PersonMatcher()
        self.n_shards = n_shards
        self.compute_replicas = compute_replicas

        self.state = DistState()
        self.counter = 0
        self.seq = 0
        self._rid_seq = 0

        # delivery bookkeeping: origin -> {seq: (wire, arrival time)}
        self.log: Dict[str, Dict[int, Tuple[Wire, float]]] = defaultdict(dict)
        self.contig: Dict[str, int] = defaultdict(int)
        self.extra: Dict[str, Set[int]] = defaultdict(set)

        # matching side
        self.norm: Dict[str, Normalized] = {}
        self.ver: Dict[str, Timestamp] = {}  # live record -> current version
        self._shard_cache: Dict[str, int] = {}
        self._rank_cache: Dict[Tuple[int, str], int] = {}
        self.alive: Set[str] = set(self.members)  # optimistic until a detector says otherwise
        self.owned: Set[int] = self._compute_owned(self.alive)
        self.block: Dict[str, Set[str]] = {}
        self.pending: Dict[str, Optional[Set[str]]] = {}  # rid -> keys to (re)match, None = all owned

        # view side
        self.partners: Dict[str, Set[str]] = defaultdict(set)
        self.adj: Dict[str, Set[str]] = {}
        self.entity_of: Dict[str, str] = {}
        self.members_of: Dict[str, Set[str]] = {}

        self.metrics = defaultdict(int)

    # ------------------------------------------------------------------
    # clocks
    # ------------------------------------------------------------------
    def _tick(self) -> int:
        self.counter = max(self.counter, self.state.max_counter) + 1
        return self.counter

    def _fresh_rid(self) -> str:
        while True:
            self._rid_seq += 1
            rid = f"{self.replica}/{self._rid_seq}"
            if self.state.version(rid) is None:
                return rid

    # ------------------------------------------------------------------
    # client operations
    # ------------------------------------------------------------------
    def submit(self, ops: Sequence[Sequence[Any]], now: float = 0.0) -> Tuple[List[Any], Optional[Delta]]:
        """Apply a batch of client operations atomically on this replica.

        Ops: ``["add", rid|None, attrs]``, ``["update", rid, attrs]``,
        ``["patch", rid, changes]``, ``["delete", rid]``, ``["link", a, b]``,
        ``["unlink", a, b]``, ``["retract_link", a, b]``,
        ``["retract_unlink", a, b]``.  Returns per-op results and the delta
        to broadcast (``None`` if nothing changed).
        """
        results: List[Any] = []
        parts: List[Wire] = []
        changes = Changes()

        def write(w: Wire) -> None:
            parts.append(w)
            changes.update(self.state.merge(w))

        for op in ops:
            kind = op[0]
            try:
                if kind == "add":
                    rid = op[1] or self._fresh_rid()
                    write({"r": [[rid, self._tick(), self.replica, dict(op[2])]]})
                    results.append(rid)
                elif kind in ("update", "patch"):
                    rid = op[1]
                    current = self.state.er.record(rid)
                    if current is None:
                        raise KeyError(f"{rid} is not a live record")
                    attrs = dict(op[2]) if kind == "update" else {**current, **op[2]}
                    write({"r": [[rid, self._tick(), self.replica, attrs]]})
                    results.append(rid)
                elif kind == "delete":
                    write({"r": [[op[1], self._tick(), self.replica, None]]})
                    results.append(op[1])
                elif kind in ("link", "unlink"):
                    a, b = pair_key(op[1], op[2])
                    write({"a": [[self.replica, self._tick(), LINK if kind == "link" else UNLINK, a, b]]})
                    results.append(True)
                elif kind in ("retract_link", "retract_unlink"):
                    a, b = pair_key(op[1], op[2])
                    which = LINK if kind == "retract_link" else UNLINK
                    tags = self.state.er.assertions.tags_of((which, a, b))
                    if tags:
                        write({"x": [[t[0], t[1]] for t in tags]})
                    results.append(len(tags))
                else:
                    raise ValueError(f"unknown op {kind!r}")
            except (KeyError, ValueError) as exc:
                results.append({"error": str(exc)})

        if not parts:
            return results, None
        self._after_merge(changes)
        self.metrics["client_ops"] += len(ops)
        return results, self._originate(combine(*parts), now)

    # ------------------------------------------------------------------
    # delivery
    # ------------------------------------------------------------------
    def has(self, origin: str, n: int) -> bool:
        return n <= self.contig[origin] or n in self.extra[origin]

    def _record(self, origin: str, n: int, wire: Wire, now: float) -> None:
        self.log[origin][n] = (wire, now)
        if n == self.contig[origin] + 1:
            c, ex = n, self.extra[origin]
            while c + 1 in ex:
                ex.remove(c + 1)
                c += 1
            self.contig[origin] = c
        else:
            self.extra[origin].add(n)

    def _originate(self, wire: Wire, now: float) -> Delta:
        self.seq += 1
        self._record(self.replica, self.seq, wire, now)
        return (self.replica, self.seq, wire)

    def receive(self, origin: str, n: int, wire: Wire, now: float = 0.0) -> bool:
        """Apply a delta from a peer (or from the local log on restart).
        Returns False, doing no work, if this delta was already held."""
        if self.has(origin, n):
            self.metrics["duplicates"] += 1
            return False
        self._record(origin, n, wire, now)
        self._after_merge(self.state.merge(wire))
        self.metrics["deltas_applied"] += 1
        return True

    def vv_summary(self) -> Dict[str, List[Any]]:
        origins = set(self.contig) | set(self.extra)
        return {o: [self.contig[o], sorted(self.extra[o])] for o in origins}

    def missing_for(
        self, summary: Dict[str, Sequence[Any]], now: float = math.inf, grace: float = 0.0, limit: int = 10**9
    ) -> List[Delta]:
        """Deltas this node holds that ``summary`` (a peer's version vector)
        lacks.  Deltas that arrived within ``grace`` seconds are skipped: they
        are almost certainly still in flight to the peer."""
        out: List[Delta] = []
        for origin, entries in self.log.items():
            peer = summary.get(origin)
            peer_contig, peer_extra = (int(peer[0]), set(peer[1])) if peer else (0, set())
            top = max(self.contig[origin], max(self.extra[origin], default=0))
            for n in range(peer_contig + 1, top + 1):
                if n in peer_extra:
                    continue
                entry = entries.get(n)
                if entry is None or now - entry[1] < grace:
                    continue
                out.append((origin, n, entry[0]))
                if len(out) >= limit:
                    return out
        return out

    # ------------------------------------------------------------------
    # sharding
    # ------------------------------------------------------------------
    def shard_of(self, key: str) -> int:
        s = self._shard_cache.get(key)
        if s is None:
            s = zlib.crc32(key.encode()) % self.n_shards
            self._shard_cache[key] = s
        return s

    def owners(self, shard: int, alive: Optional[Set[str]] = None) -> List[str]:
        alive = self.alive if alive is None else alive
        ranked = sorted(alive, key=lambda m: self._rank(shard, m), reverse=True)
        return ranked[: self.compute_replicas]

    def _rank(self, shard: int, member: str) -> int:
        r = self._rank_cache.get((shard, member))
        if r is None:
            r = self._rank_cache[(shard, member)] = _rank(shard, member)
        return r

    def _compute_owned(self, alive: Set[str]) -> Set[int]:
        return {s for s in range(self.n_shards) if self.id in self.owners(s, alive)}

    def set_alive(self, alive: Iterable[str]) -> bool:
        """Install a new membership view.  Returns True if ownership changed."""
        alive_set = (set(alive) & set(self.members)) | {self.id}
        if alive_set == self.alive:
            return False
        self.alive = alive_set
        new_owned = self._compute_owned(alive_set)
        gained = new_owned - self.owned
        changed = new_owned != self.owned
        self.owned = new_owned
        self.block = {}
        for rid, nn in self.norm.items():
            gained_keys: Optional[Set[str]] = None
            for k in nn.keys:
                s = self.shard_of(k)
                if s in new_owned:
                    self.block.setdefault(k, set()).add(rid)
                    if s in gained:
                        gained_keys = gained_keys or set()
                        gained_keys.add(k)
            if gained_keys:
                if rid in self.pending:
                    cur = self.pending[rid]
                    if cur is not None:
                        cur |= gained_keys
                else:
                    self.pending[rid] = gained_keys
        self.metrics["membership_changes"] += 1
        return changed

    # ------------------------------------------------------------------
    # matching
    # ------------------------------------------------------------------
    def has_pending(self) -> bool:
        return bool(self.pending)

    def step_matching(
        self,
        budget_s: Optional[float] = None,
        max_records: Optional[int] = None,
        now: float = 0.0,
        clock: Callable[[], float] = time.perf_counter,
    ) -> Optional[Delta]:
        """Match up to ``max_records`` pending records (or for ``budget_s``
        seconds) inside owned blocks.  Returns a fact delta to broadcast."""
        deadline = None if budget_s is None else clock() + budget_s
        done = 0
        found: List[Fact] = []
        st = self.state
        while self.pending:
            if max_records is not None and done >= max_records:
                break
            if deadline is not None and done and clock() >= deadline:
                break
            rid = next(iter(self.pending))
            keys = self.pending.pop(rid)
            done += 1
            nr = self.norm.get(rid)
            if nr is None:
                continue
            ts_r = st.live_version(rid)
            for k in (nr.keys if keys is None else keys & nr.keys):
                if self.shard_of(k) not in self.owned:
                    continue
                for c in self.block.get(k, ()):
                    if c == rid:
                        continue
                    if c in self.pending:
                        ck = self.pending[c]
                        if ck is None or k in ck:
                            continue  # c is queued for this key and will compare against rid itself
                    nc = self.norm[c]
                    if min(nr.keys & nc.keys) != k:
                        continue  # evaluated under the pair's smallest shared key instead
                    self.metrics["comparisons"] += 1
                    if self.matcher.match(nr, nc):
                        f = make_fact(rid, ts_r, c, st.live_version(c))  # type: ignore[arg-type]
                        if f not in st.facts:
                            found.append(f)
        self.metrics["records_matched"] += done
        if not found:
            return None
        wire: Wire = {"f": [list(f) for f in found]}
        self._after_merge(st.merge(wire))
        self.metrics["facts_emitted"] += len(found)
        return self._originate(wire, now)

    # ------------------------------------------------------------------
    # incremental maintenance after any merge
    # ------------------------------------------------------------------
    def _after_merge(self, ch: Changes) -> None:
        if not ch:
            return
        er = self.state.er
        for a, b in ch.pairs:
            if er.has_assertion(LINK, a, b) or er.has_assertion(UNLINK, a, b):
                self.partners[a].add(b)
                self.partners[b].add(a)
            else:
                self.partners[a].discard(b)
                self.partners[b].discard(a)

        hard: Set[str] = set()  # records whose existing edges may disappear
        fresh: List[str] = []   # records that were not live before: edges can only appear
        for a, b in ch.pairs:
            hard.add(a)
            hard.add(b)
        for rid in ch.records:
            old = self.norm.pop(rid, None)
            if old is not None:
                hard.add(rid)
                for k in old.keys:
                    bucket = self.block.get(k)
                    if bucket is not None:
                        bucket.discard(rid)
                        if not bucket:
                            del self.block[k]
            attrs = er.record(rid)
            if attrs is None:
                self.ver.pop(rid, None)
                self.pending.pop(rid, None)
                continue
            self.ver[rid] = self.state.live_version(rid)  # type: ignore[assignment]
            if old is None:
                fresh.append(rid)
            nn = self.matcher.normalize(attrs)
            self.norm[rid] = nn
            owned_any = False
            for k in nn.keys:
                if self.shard_of(k) in self.owned:
                    self.block.setdefault(k, set()).add(rid)
                    owned_any = True
            if owned_any:
                self.pending[rid] = None
            else:
                self.pending.pop(rid, None)

        if hard:
            self._refresh_view(hard)
        for rid in fresh:
            if rid in hard or rid not in self.ver:
                continue
            self.adj[rid] = set()
            self.entity_of[rid] = rid
            self.members_of[rid] = {rid}
            for c in self._view_candidates(rid):
                if c in self.adj and self._edge(rid, c):
                    self._add_edge(rid, c)
        for f in ch.facts:
            a, b = f[0], f[3]
            if a in self.adj and b in self.adj and b not in self.adj[a] and self._edge(a, b):
                self._add_edge(a, b)

    def _add_edge(self, a: str, b: str) -> None:
        """Insert one edge and union the two entities (no recomputation)."""
        self.adj[a].add(b)
        self.adj[b].add(a)
        ea, eb = self.entity_of[a], self.entity_of[b]
        if ea == eb:
            return
        ma, mb = self.members_of[ea], self.members_of[eb]
        if len(ma) < len(mb):
            ea, eb, ma, mb = eb, ea, mb, ma
        # fold the smaller member set into the larger one
        del self.members_of[eb]
        ma |= mb
        new_eid = min(ea, eb)
        if new_eid == ea:
            for r in mb:
                self.entity_of[r] = ea
        else:  # the entity id is the minimum member, which came from the smaller side
            del self.members_of[ea]
            self.members_of[new_eid] = ma
            for r in ma:
                self.entity_of[r] = new_eid

    def _edge(self, r: str, c: str) -> bool:
        vr, vc = self.ver.get(r), self.ver.get(c)
        if vr is None or vc is None:
            return False
        st = self.state
        partners = self.partners.get(r)
        if partners and c in partners:
            a, b = pair_key(r, c)
            if st.er.has_assertion(UNLINK, a, b):
                return False
            if st.er.has_assertion(LINK, a, b):
                return True
        return make_fact(r, vr, c, vc) in st.facts

    def _view_candidates(self, r: str) -> Set[str]:
        out = set(self.partners.get(r, ()))
        for f in self.state.facts_by_rid.get(r, ()):
            out.add(f[3] if f[0] == r else f[0])
        out.discard(r)
        return out

    def _refresh_view(self, touched: Set[str]) -> None:
        affected: Set[str] = set()
        for r in touched:
            eid = self.entity_of.get(r)
            if eid is not None:
                affected.add(eid)
            for nb in self.adj.pop(r, ()):
                self.adj[nb].discard(r)
        live_touched = [r for r in touched if r in self.ver]
        for r in live_touched:
            self.adj[r] = set()
        for r in live_touched:
            for c in self._view_candidates(r):
                if c in self.adj and self._edge(r, c):
                    self.adj[r].add(c)
                    self.adj[c].add(r)
                    eid = self.entity_of.get(c)
                    if eid is not None:
                        affected.add(eid)
        nodes: Set[str] = set(live_touched)
        for eid in affected:
            nodes |= self.members_of.pop(eid, set())
        for r in nodes:
            self.entity_of.pop(r, None)
        nodes = {r for r in nodes if r in self.adj}
        unvisited = set(nodes)
        while unvisited:
            start = unvisited.pop()
            comp = {start}
            stack = [start]
            while stack:
                x = stack.pop()
                for nb in self.adj[x]:
                    if nb not in comp:
                        assert nb in nodes, (x, nb)
                        comp.add(nb)
                        unvisited.discard(nb)
                        stack.append(nb)
            eid = min(comp)
            self.members_of[eid] = comp
            for r in comp:
                self.entity_of[r] = eid

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def entity_of_record(self, rid: str) -> Optional[str]:
        return self.entity_of.get(rid)

    def entities(self) -> Dict[str, FrozenSet[str]]:
        return {eid: frozenset(m) for eid, m in self.members_of.items()}

    def view_hash(self) -> str:
        h = hashlib.blake2b(digest_size=12)
        for eid in sorted(self.members_of):
            h.update(eid.encode())
            h.update(b"=")
            h.update(",".join(sorted(self.members_of[eid])).encode())
            h.update(b";")
        return h.hexdigest()

    def stats(self) -> Dict[str, Any]:
        return {
            "node": self.id,
            "replica": self.replica,
            "alive": sorted(self.alive),
            "owned_shards": len(self.owned),
            "records": len(self.norm),
            "facts": len(self.state.facts),
            "entities": len(self.members_of),
            "pending": len(self.pending),
            "vv": {o: c for o, c in self.contig.items()},
            "gaps": sum(len(e) for e in self.extra.values()),
            "metrics": dict(self.metrics),
        }

    # ------------------------------------------------------------------
    # self-check
    # ------------------------------------------------------------------
    def check_invariants(self) -> None:
        """The incremental view equals a from-scratch evaluation of the same
        edge rule over this replica's *current* state (facts included)."""
        st = self.state
        live = {rid for rid, _ in st.er.records.live_items()}
        assert set(self.norm) == live, "normalised set != live records"
        assert set(self.adj) == live, "adjacency keys != live records"
        assert self.ver == {r: st.live_version(r) for r in live}, "version cache diverged"
        pairs = {(f[0], f[3]) for f in st.facts}
        pairs |= {(a, b) for _, a, b in st.er.assertions.elements()}
        expect_adj: Dict[str, Set[str]] = {r: set() for r in live}
        for a, b in pairs:
            if a in live and b in live and self._edge(a, b):
                expect_adj[a].add(b)
                expect_adj[b].add(a)
        assert self.adj == expect_adj, "adjacency diverged from from-scratch rule"
        seen: Set[str] = set()
        expect: Dict[str, FrozenSet[str]] = {}
        for s in live:
            if s in seen:
                continue
            comp, stack = {s}, [s]
            while stack:
                x = stack.pop()
                for nb in expect_adj[x]:
                    if nb not in comp:
                        comp.add(nb)
                        stack.append(nb)
            seen |= comp
            expect[min(comp)] = frozenset(comp)
        assert self.entities() == expect, "entity view diverged"
        for f in st.facts:
            assert not st.fact_is_stale(f), "stale fact retained"
        for k, bucket in self.block.items():
            assert self.shard_of(k) in self.owned and bucket
