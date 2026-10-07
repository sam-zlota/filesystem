# crdt_er: dynamic, incremental entity resolution as a CRDT

A dependency-free Python prototype of an entity-resolution (ER) engine whose
replicated state is a **join-semilattice**, so any number of replicas can
ingest, correct and curate records concurrently and still converge to the
same clustering. Resolution is **incremental**: each change only re-examines
the records it touches and the handful of clusters they belong to.

```
pip install -r requirements.txt           # msgpack, used only by the distributed mode
python demo.py                            # single-process walk-through
python ha_demo.py                         # 3-process cluster: kill, partition, restart
python bench.py --nodes 1 2 4 --verify    # throughput vs cluster size
python -m unittest discover -s tests -t . # 27 tests
```

The first half of this document describes the CRDT core. The second half,
**Distributed mode**, describes how it runs as a multi-node cluster.

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
| `crdt_er/dist/` | Distributed mode: replicated state with match facts, node logic, asyncio server, client, log, simulator, launcher. |
| `tests/` | Lattice laws, conflict scenarios, randomized convergence, distributed chaos simulation, a real multi-process cluster test. |

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

---

# Distributed mode

`crdt_er.dist` runs the same CRDT as a cluster of OS processes that talk over
TCP. Every node accepts reads and writes, and no node is a leader. Nodes keep
working through crashes and network partitions, and they converge once
messages flow again.

## Architecture

```
            clients (any node; fail over to the next on error)
               |                 |                 |
         +-----v-----+     +-----v-----+     +-----v-----+
         |  node n0  |<--->|  node n1  |<--->|  node n2  |   full mesh, TCP
         |-----------|     |-----------|     |-----------|
         | state     |     | state     |     | state     |   every node: all records,
         | view      |     | view      |     | view      |   assertions, facts, entities
         | shards A  |     | shards B  |     | shards C  |   matching: own shards only
         | log       |     | log       |     | log       |
         +-----------+     +-----------+     +-----------+
```

* **Shared state is replicated, not centralised.** Each node holds a replica of
  the lattice: the record map, the assertion set, and a set of **match facts**.
  Nothing is locked or coordinated, and every change travels as a delta.
* **Matching is partitioned.** Blocking keys hash into shards, and shards map to
  live nodes by rendezvous hashing. A node compares records only inside the
  blocks it owns. A pair that shares several keys is compared only under its
  smallest shared key, so the cluster does every comparison exactly once.
* **Results are shared as facts.** A fact says "version x of record a matches
  version y of record b". It is immutable, and it is valid only while both
  versions are current. Version numbers only grow, so a stale fact can never
  become valid again, and dropping stale facts keeps the merge a lattice join.
  Every node builds the same entity view from the same facts.
* **Each node is one event loop.** There are no threads or locks inside a node.
  Parallelism comes from running more processes.

## Delivery, recovery and durability

| Mechanism | What it handles |
| --- | --- |
| Delta ids `(origin replica, seq)` and version vectors | Duplicates are detected before any work is done. Nodes know exactly which deltas they lack. |
| Anti-entropy: periodic version-vector exchange with a random peer | Lost messages, a peer that was down, and partitions. The peer resends only the missing deltas. |
| Write-ahead log, written before acknowledging or forwarding | Restarts. A node replays its log, and a torn final frame is truncated. |
| Incarnation counter, fsynced at startup and part of the replica id | A node that lost its log tail cannot reissue a timestamp or tag that peers already hold. |
| Heartbeat failure detector, then shard reassignment and rescan | A dead node's matching work moves to survivors within about 1.5 seconds. |
| Client fail-over with client-chosen record ids | Writes keep succeeding while any node is up. Retrying is harmless because a re-sent add is just another version of the same record. |

Nodes can briefly disagree about who owns a shard. Then two nodes match the
same block, or for a moment nobody does. Both cases are safe. Duplicate facts
are identical because the matcher is deterministic, and a shard's new owner
always rescans it.

## Measured on this prototype

These numbers come from 4 CPU cores shared by every node process and the load
generator. There are 20,000 records, 256 shards, and `--verify` passes on every
row, meaning the final view equals the batch oracle.

| Nodes | Normal blocks: rec/s | Speedup | Large blocks: rec/s | Speedup |
| --- | --- | --- | --- | --- |
| 1 | 2,400 | 1.0x | 1,040 | 1.0x |
| 2 | 4,370 | 1.6x | 1,850 | 1.8x |
| 4 | 5,590 | 2.3x | 3,000 | 2.9x |

"Resolved" means every node has finished matching and holds the same entity
view. Comparisons split evenly across nodes. Scaling stays below linear for two
reasons. Every node still applies every record and fact to its own view, which
takes about 1 CPU-second per 20,000 records. And the 4-node run had 5 busy
processes on 4 cores. The more each record costs to match, the closer the
speedup gets to linear, which is why the large-block column scales better. A
real deployment with an ML scoring model would sit further in that direction.

The writes are acknowledged at 15,000 to 22,000 records/s on any cluster size,
because acknowledging a write never waits for matching.

`ha_demo.py` on 3 processes, abridged:

```
[ 2.3s] SIGKILL n1 (writes in flight to n1 fail over to other nodes)
[ 4.8s] survivors see alive=['n0', 'n2']; they now own 64/64 shards between them
[ 4.8s] partition: n0 | n2 (both sides keep accepting writes)
[ 7.3s] during partition: n0 has 8560 records, n2 has 7520; each owns 64 and 64 shards
[ 8.6s] n1 restarted as n1#2 with 2920 records from its log
[ 9.4s] writer done: 12000 records acked ..., 0 failed batches, 1 client failovers
[10.7s] all nodes converged 1.4s after the last write: 12000 records, 3942 entities
[15.2s] identical state on all nodes: True; entities equal batch oracle: True
```

## How it is tested

* `tests/test_dist.py` drives the node logic through a deterministic
  in-process simulator. It runs 33 chaos histories with crashes that lose log
  tails, restarts, partitions, message loss and duplication, and nodes that
  disagree about membership. Every one must end with identical state on all
  nodes and entities equal to the batch oracle. It also checks that each
  candidate pair is compared exactly once across the cluster. Deliberately
  breaking stale-fact removal, shard takeover, incarnations, anti-entropy or
  the pair deduplication makes these tests fail.
* `tests/test_dist_cluster.py` runs the kill, partition and restart scenario on
  real processes, and checks log truncation and incarnation persistence.

## What a production version would change

| Prototype | Production direction |
| --- | --- |
| Full replication of records to every node | Replicate each record to its home shard and the shards of its blocking keys, so memory and per-node apply cost also scale out. The view then needs a partitioned connected-components layer. |
| Delta history kept in memory and in an ever-growing log | Compact once every node has a delta (causal stability), and bootstrap new nodes from a snapshot. |
| Static member list and a timeout-based failure detector | Gossip membership such as SWIM, with dynamic join and leave. |
| Hand-rolled TCP mesh | A log or messaging layer such as Kafka, NATS or Redpanda for transport, and RocksDB for local state. The CRDT merge is unchanged. |
| Pure-Python Jaro-Winkler | A native implementation such as rapidfuzz, or a model server. Every node must use the same version, because convergence depends on the matcher being deterministic. |
| Entity id is the minimum record id | A stable public entity id layered on top, because the minimum changes when clusters merge or split. |
| A write is acknowledged once it is in one node's log | A write quorum (wait for k peer logs) closes the window where a node dies after acknowledging but before forwarding. |
