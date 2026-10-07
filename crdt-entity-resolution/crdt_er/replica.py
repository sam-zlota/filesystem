"""A replica = a Yjs document (pycrdt) + an incremental resolver over it.

Replicated state (the CRDT; merged by Yjs):

    records      Map<record_id, Map<field, str>>   per-field last-writer-wins
    must_link    Map<"a\\x1fb", author>             add/remove map of evidence
    cannot_link  Map<"a\\x1fb", author>
    meta         Map{"config": fingerprint}

Derived state (NOT replicated; recomputed locally): the resolver. Change
events from Yjs (local edits and remote updates alike) mark record ids and
pairs dirty; after each transaction the replica reads their *current* values
and hands them to the resolver as a delta.
"""

from __future__ import annotations

from typing import Mapping, Optional

from pycrdt import Doc, Map

from .clustering import Pair
from .config import DEFAULT_CONFIG, ResolverConfig
from .engine import IncrementalResolver, canonical_pair

SEP = "\x1f"


class ConfigMismatch(RuntimeError):
    pass


def _pair_key(a: str, b: str) -> str:
    x, y = canonical_pair(a, b)
    return f"{x}{SEP}{y}"


class Replica:
    def __init__(self, replica_id: int, config: ResolverConfig = DEFAULT_CONFIG):
        self.replica_id = replica_id
        self.config = config
        # Yjs client ids must be unique per replica (they order concurrent ops).
        self.doc = Doc(client_id=replica_id)
        self._records = self.doc.get("records", type=Map)
        self._must = self.doc.get("must_link", type=Map)
        self._cannot = self.doc.get("cannot_link", type=Map)
        self._meta = self.doc.get("meta", type=Map)
        self.resolver = IncrementalResolver(config)

        self._dirty_records: set[str] = set()
        self._dirty_pairs: set[str] = set()
        self._subs = [
            self._records.observe_deep(self._on_records),
            self._must.observe(self._on_links),
            self._cannot.observe(self._on_links),
        ]
        with self.doc.transaction():
            self._meta["config"] = config.fingerprint()

    # ------------------------------------------------------- change tracking

    def _on_records(self, events) -> None:
        for e in events:
            if e.path:  # field-level change inside a record map
                self._dirty_records.add(e.path[0])
            else:  # record added / replaced / deleted
                self._dirty_records.update(e.keys)

    def _on_links(self, event) -> None:
        self._dirty_pairs.update(event.keys)

    def _flush(self) -> set[str]:
        recs: dict[str, Optional[dict]] = {}
        for rid in self._dirty_records:
            m = self._records.get(rid)
            recs[rid] = None if m is None else m.to_py()
        links: dict[Pair, tuple[bool, bool]] = {}
        for key in self._dirty_pairs:
            a, b = key.split(SEP)
            links[(a, b)] = (key in self._must, key in self._cannot)
        self._dirty_records.clear()
        self._dirty_pairs.clear()
        return self.resolver.apply(recs, links)

    def _reconcile(self) -> None:
        """Mark dirty every record/pair whose doc value differs from the
        resolver's input. O(n) scan; the re-clustering itself stays local."""
        records, must, cannot = self.state()
        res = self.resolver
        for rid in records.keys() | res.records.keys():
            if records.get(rid) != res.records.get(rid):
                self._dirty_records.add(rid)
        for a, b in (must ^ res.must) | (cannot ^ res.cannot):
            self._dirty_pairs.add(f"{a}{SEP}{b}")

    # ------------------------------------------------------------ local ops

    def upsert(self, rid: str, fields: Mapping[str, Optional[str]]) -> set[str]:
        """Create a record or update some of its fields (None deletes a field)."""
        if SEP in rid:
            raise ValueError("record id contains reserved separator")
        with self.doc.transaction():
            rec = self._records.get(rid)
            if rec is None:
                self._records[rid] = Map({k: v for k, v in fields.items() if v is not None})
            else:
                for k, v in fields.items():
                    if v is None:
                        if k in rec:
                            del rec[k]
                    elif rec.get(k) != v:
                        rec[k] = v
        return self._flush()

    def remove(self, rid: str) -> set[str]:
        with self.doc.transaction():
            if rid in self._records:
                del self._records[rid]
        return self._flush()

    def assert_match(self, a: str, b: str, author: str = "") -> set[str]:
        return self._set_evidence(a, b, self._must, self._cannot, author)

    def assert_non_match(self, a: str, b: str, author: str = "") -> set[str]:
        return self._set_evidence(a, b, self._cannot, self._must, author)

    def retract(self, a: str, b: str) -> set[str]:
        key = _pair_key(a, b)
        with self.doc.transaction():
            for m in (self._must, self._cannot):
                if key in m:
                    del m[key]
        return self._flush()

    def _set_evidence(self, a, b, add_to: Map, remove_from: Map, author: str) -> set[str]:
        key = _pair_key(a, b)
        with self.doc.transaction():
            add_to[key] = author or str(self.replica_id)
            if key in remove_from:  # this replica changed its mind
                del remove_from[key]
        return self._flush()

    # ------------------------------------------------------------------ sync

    def state_vector(self) -> bytes:
        return self.doc.get_state()

    def changes_since(self, state_vector: bytes = b"\x00") -> bytes:
        return self.doc.get_update(state_vector)

    def receive(self, update: bytes, ordered: bool = True) -> set[str]:
        """Apply a remote update. At the CRDT level this is idempotent,
        commutative and order-tolerant (Yjs buffers updates whose causal
        dependencies have not arrived yet).

        The derived view is driven by Yjs change events, and those are only
        reliable under causal delivery: when a buffered update is integrated
        later, yrs emits no event for it, and a delete can arrive as an event
        with no keys. ``sync()`` (state-vector anti-entropy) and the standard
        y-protocols sync both deliver causally. For any other transport pass
        ``ordered=False``: the replica then diffs the whole document against
        the resolver's inputs (O(n) scan; re-clustering stays local).
        """
        self.doc.apply_update(update)
        if not ordered:
            self._reconcile()
        changed = self._flush()
        theirs = self._meta.get("config")
        if theirs != self.config.fingerprint():
            raise ConfigMismatch(f"replica {self.replica_id}: peer config {theirs}")
        return changed

    def sync(self, other: "Replica") -> None:
        """Bidirectional anti-entropy exchange of missing updates."""
        to_other = self.changes_since(other.state_vector())
        to_self = other.changes_since(self.state_vector())
        other.receive(to_other)
        self.receive(to_self)

    # --------------------------------------------------------------- queries

    def state(self) -> tuple[dict, set[Pair], set[Pair]]:
        def pairs(m: Map) -> set[Pair]:
            return {tuple(k.split(SEP)) for k in m.keys()}

        return self._records.to_py() or {}, pairs(self._must), pairs(self._cannot)

    def entities(self) -> list[dict]:
        return self.resolver.entities()

    def entity_of(self, rid: str) -> dict:
        return self.resolver.entity(self.resolver.cluster_of[rid])

    def conflicts(self) -> list[Pair]:
        return self.resolver.conflicts()
