# crdt_er: dynamic, incremental entity resolution as a CRDT

A dependency-free Python prototype of an entity-resolution (ER) engine whose
replicated state is a **join-semilattice**, so any number of replicas can
ingest, correct and curate records concurrently and still converge to the
same clustering. Resolution is **incremental**: each change only re-examines
the records it touches and the handful of clusters they belong to.

```
python demo.py                            # narrated walk-through
python -m unittest discover -s tests -t . # 16 tests, incl. randomized convergence
```

## The idea in one paragraph

Only the *inputs* to resolution are replicated, and they live in CRDTs:
records in a last-writer-wins map, curator assertions (`link` / `unlink`) in
an observed-remove set. The clustering itself is never replicated. It is a
deterministic function of that state: connected components of the graph whose
edges are given by a pure `decide(a, b)` rule. Because the state converges
and the function is deterministic, the clustering converges, and so do the
entity ids (an entity is named by its smallest record id). Each replica keeps
the clustering materialised and repairs it locally as deltas arrive.

## Layout

| File | Role |
| --- | --- |
| `crdt_er/lattice.py` | `LWWMap` (pointwise-max join, tombstones) and `ORSet` (add-wins observed-remove set). |
| `crdt_er/state.py` | `ERState = LWWMap[records] x ORSet[assertions]`; `merge` is the lattice join and doubles as delta application. |
| `crdt_er/matching.py` | `Matcher` protocol (normalise + blocking keys + symmetric `match`), Jaro-Winkler, and a rule-based `PersonMatcher`. |
| `crdt_er/engine.py` | `Replica`: local ops that emit deltas, `merge`, the incremental `_refresh`, queries, `check_invariants`, and the batch oracle `resolve_from_scratch`. |
| `crdt_er/simulate.py` | `Network`: random-order, duplicating delivery plus full-state anti-entropy. |
| `tests/` | Lattice laws on random histories, conflict scenarios, 20-seed randomized convergence. |

## Replicated state

**Records**: `record_id -> (timestamp, attrs | None)`. Timestamps are
`(lamport, replica_id)`, which is a total order, so concurrent writes have a
deterministic winner. An update replaces the whole attribute dict; a delete
writes `None`. Merge keeps the greater timestamp per key.

**Assertions**: elements `("link", a, b)` and `("unlink", a, b)` with `a < b`.
Each add carries a unique `(replica, seq)` tag. A retraction tombstones the
tags the retracting replica had observed, so a concurrent re-assertion with a
fresh tag survives (add-wins). Merge is union of adds and union of tombstones.

**Deltas**: a local op returns a tiny `ERState` holding only what it changed.
Replicas apply it with the same `merge` they use for full-state sync, so
delivery order, duplication and mixing delta traffic with gossip all work.
Delta *loss* is the one thing delta traffic alone cannot absorb; `Network.anti_entropy`
shows the full-state sync that covers it.

## Edge rule and conflict semantics

```
decide(a, b):
    unlink asserted on (a, b)  -> no edge     # curator "different people" always wins
    link   asserted on (a, b)  -> edge
    otherwise                  -> matcher.match(norm(a), norm(b))
```

| Concurrent situation | Outcome on every replica |
| --- | --- |
| `link` vs `unlink` on the same pair | unlinked (unlink dominates while live) |
| `update` vs `delete` of a record | whichever has the greater `(lamport, replica)` timestamp |
| `retract link` vs a fresh `link` | linked (observed-remove: only observed tags are removed) |
| two `update`s of one record | the later timestamp, whole-record |
| record deleted, later re-added under the same id | old assertions on that id still apply |

## Incremental maintenance

`Replica.merge` reports the records and pairs that actually changed. `_refresh`
then:

1. detaches those records from the blocking index and the match graph and notes
   which entities they belonged to;
2. re-normalises the ones that are still live and re-inserts them;
3. re-evaluates only edges incident to them, found through shared blocking keys
   plus any records they share an assertion with;
4. re-runs connectivity over exactly the union of the affected entities and
   reassigns entity ids.

Unaffected clusters are never visited. Deletions and splits (which plain
union-find cannot express) are handled by this bounded local recomputation.
`check_invariants()` compares the materialised view with `resolve_from_scratch`,
and the randomized test does this after operations throughout the run.

## What the tests establish

* `test_lattice.py`: on random concurrent histories, `merge` is commutative,
  associative and idempotent, and the join is an upper bound of its operands.
* `test_engine.py`: automatic matching, transitive clusters that split when a
  bridge record is deleted, updates that move a record between entities, the
  precedence of unlink over link over matcher, order-/duplicate-insensitive
  delivery, and the concurrent conflicts in the table above.
* `test_convergence.py`: 4 replicas, 250 random ops each run, 25% duplicated
  deliveries in random order, across 20 seeds. Asserts the replicas really
  diverged mid-run, that every replica's incremental view equals the batch
  oracle, that after quiescence all states and clusterings are identical, and
  that a late joiner syncing by full state agrees.

## Assumptions and limits (it is a prototype)

* All replicas must run the same matcher version; determinism of `match` is
  what makes derived edges agree. Changing matching rules is a coordinated
  deployment event, not something the lattice handles.
* Blocking keys bound which pairs are compared; a matching pair that shares no
  key is simply not found. This affects recall, not convergence.
* Whole-record LWW. Per-field registers or multi-value registers would be a
  drop-in change in `state.py`.
* Tombstones and ORSet tags are retained forever; a causal-stability GC is
  future work.
* Entity ids are deterministic but not stable across merges and splits
  (they are the minimum member id). A stable public id would be a separate
  mapping layered on top.
* Component recomputation is bounded by the size of the affected clusters;
  pathological hub records make that bound large. A dynamic-connectivity
  structure would tighten it.
