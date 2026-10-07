# Design: CRDT-convergent, dynamic, incremental entity resolution

## 1. Problem and goals

Entity resolution (ER) groups records that refer to the same real-world entity.
This prototype targets the hard combination of:

| Requirement | Meaning here |
|---|---|
| **Dynamic** | Records are inserted, **edited and deleted**. Human feedback ("same" / "not same") is added and **retracted**. Entities must merge *and split* as a result. |
| **Incremental** | A change costs work proportional to the part of the graph it affects, not to the whole dataset. |
| **CRDT** | Any number of replicas accept writes while offline/partitioned, exchange updates in any order, any number of times, and are guaranteed to converge to the **same entities** (strong eventual consistency) with no coordination or consensus. |

## 2. Core idea: replicate evidence, derive entities

The obvious approaches fail:

* **Replicating cluster assignments** (e.g. LWW register "record → entity") is
  convergent but meaningless: concurrent merges/splits clobber each other and
  break transitivity (A→E1, B→E1 on one replica, B→E2 on another).
* **Partition-lattice CRDT** (an entity partition where merge = join of
  equivalence relations) is a genuine CRDT, but it is *monotone*: it can only
  merge. Edits, deletions and "not the same" feedback require **splits**, which
  are not expressible as joins.
* **Replaying an op log in a total order** (e.g. sorted by HLC) converges, but
  incremental clustering is order-dependent, so every late-arriving op forces a
  rollback and replay.

The design used instead splits the system into two layers:

```
 ┌────────────────────────── replicated (CRDT) ───────────────────────────┐
 │  records:     Map<record_id, Map<field, str>>   (Yjs, per-field LWW)    │
 │  must_link:   Map<pair, author>                 (Yjs add/remove map)    │
 │  cannot_link: Map<pair, author>                                         │
 │  meta:        {config: fingerprint}                                     │
 └──────────────────────────────┬──────────────────────────────────────────┘
                change events   │  (keys touched; current values re-read)
 ┌──────────────────────────────▼──────── local, derived ──────────────────┐
 │ normalize → block (MinHash LSH + exact keys) → score (Fellegi–Sunter)   │
 │ → candidate graph G → per-component constrained clustering → entities   │
 └─────────────────────────────────────────────────────────────────────────┘
```

1. **The CRDT holds only facts**: record contents and human assertions.
   Their merge is handled by a well-tested CRDT library (Yjs via `pycrdt`).
2. **Entities are a pure, deterministic function** `F(state, config)`.
   Nothing order-dependent, random, or replica-specific goes into `F`.
3. **The resolver keeps `F` up to date incrementally**, under one invariant:
   *the incrementally maintained view equals `F` recomputed from scratch.*

**Convergence argument.** Yjs guarantees that replicas that have delivered the
same set of updates hold equal state `S`. By (2), `F(S)` is then equal too. By
(3), each replica's materialized view equals its `F(S)`. So the entity view
inherits strong eventual consistency from the CRDT underneath it. It doesn't
need its own merge function, and merges and splits are equally easy because
the view is non-monotone by construction.

Both premises are checked by property-based tests (section 8).

## 3. Replicated state and conflict semantics

The library is **pycrdt** (Python bindings to `yrs`, the Rust port of Yjs). It
gives state-vector based delta sync, idempotent/commutative update application,
and buffering of out-of-order updates. Automerge was the alternative; Yjs was
picked for its mature Python bindings and its sync protocol.

| Concurrent situation | Outcome | Why |
|---|---|---|
| Two replicas edit *different* fields of a record | both edits kept | each record is a nested `Map`; Yjs merges per key |
| Two replicas edit the *same* field | one value wins, same on all replicas | Yjs LWW-style key resolution (ordered by client id) |
| Edit vs. delete of a record | delete wins | the nested map is tombstoned; edits inside it are dropped |
| Re-create a deleted record id | allowed | a new map is set under the key |
| `assert_match` vs. concurrent `assert_non_match` on a pair | **both** facts survive; **cannot-link wins** in the view, and the pair is reported by `conflicts()` | must/cannot live in *separate* maps rather than one LWW register, so disagreement is surfaced instead of being silently decided by a client-id tiebreak. A wrong merge is usually costlier than a missed one. |
| Assertion about a record that doesn't exist (yet) | kept, dormant | evidence is stored independently of records; it activates when both endpoints are live |
| A replica changes its mind (`match` → `non_match`) | its own old assertion is deleted in the same transaction | |

Config (thresholds, weights, blocking params) is **not** replicated data. Its
fingerprint is, and `receive()` raises `ConfigMismatch` if peers disagree.

## 4. Derived view pipeline (all deterministic)

### 4.1 Normalization
Unicode NFKD + accent strip, casefold, punctuation → space, whitespace collapse.
Phones use the last 10 digits, and only when there are at least 7.

### 4.2 Blocking / candidate generation (incremental)
Comparing all O(n²) pairs is out of the question, so each record gets candidates from two
indexes that support **insert and remove** (records change):

* **MinHash LSH** (`datasketch.MinHashLSH`, 64 permutations, Jaccard threshold
  0.4) over character 3-grams of the *token-sorted* name, which catches
  "Jon Smith" / "John Smith" / "Smith, John".
* **Exact inverted index** on normalized email and phone.

Key property: whether *(a, b)* is a candidate pair depends **only on a and b**
(they share an LSH band bucket or an exact key). It does not depend on insertion
order or on other records, so all replicas derive the same candidate pairs.
MinHash uses seeded permutations and SHA-1 token hashing, never Python's
per-process randomized `hash()`. A test runs the resolver under different
`PYTHONHASHSEED`s to check this.

Blocking misses some true matches (LSH is probabilistic). That costs recall but
**not** convergence, because every replica misses the same ones.

### 4.3 Pairwise scoring
A simplified **Fellegi–Sunter** additive score: each field present in *both*
records adds `disagree + level·(agree − disagree)`, where `level ∈ [0,1]` comes
from a comparator (`rapidfuzz` token-sort / token-set ratio for names and
addresses, exact match for email and phone). Missing values add 0. Absence isn't
evidence.

| field | agree | disagree | comment |
|---|---|---|---|
| name | +4 | −4 | fuzzy, ramps 0.70 → 0.95 |
| email | +7 | −1 | agreement is strong; disagreement is weak (people have many emails) |
| phone | +6 | −1 | |
| address | +3 | −0.5 | fuzzy |

Match threshold: **6.0**. As a result, the same name alone (4) is not enough,
same name + same email (11) is, and a shared family email with a different
name (−4 + 7 = 3) isn't. Scores are computed with records in canonical id
order, so they are symmetric. They are rounded to 3 decimals so that last-bit
float differences between platforms cannot flip a threshold decision (this
guards against the problem but does not rule it out; see section 9).

### 4.4 Candidate graph G
Vertices = live records; edges = scored pairs ≥ threshold, plus must-links
whose endpoints are both live. Cannot-links are **not** edges. They only
constrain clustering inside a component.

### 4.5 Clustering: constrained greedy (Kruskal-style) per connected component
Within each connected component of G:

1. union along must-links, in canonical id order;
2. then along match edges by descending score (ties broken by ids);
3. skip any union that would put a cannot-link pair in the same cluster.

This is a union-find, with a member set per root for constraint checks. The
result depends only on the *set* of edges and constraints, never on discovery
order. A must-link that loses to a cannot-link (directly or through a
transitive chain) is reported by `conflicts()` for human review.

Cluster / entity id = smallest member record id (deterministic).

### 4.6 Survivorship (golden record)
For each field, the most frequent value among members wins; ties go to the
lexicographically smallest value. The result is deterministic and order-free.

## 5. Incremental maintenance

**Locality lemma.** Clustering is computed independently per connected
component of G, and depends only on that component's edges and constraints.
So a change can only alter clusters in components that contain a record whose
edges, constraints or liveness changed (a *dirty* record).

`IncrementalResolver.apply(delta)` takes **current values** (not operations)
for touched record ids and pairs. That makes it idempotent and lets deltas be
coalesced. It then:

1. For each changed record: remove its old edges (old neighbours become dirty),
   update the LSH and exact indexes, query candidates, score them, and add new
   edges (new neighbours become dirty). Must-link neighbours become dirty too,
   because their G-adjacency changes when a record appears or disappears.
2. For each changed pair: update must/cannot sets; both endpoints become dirty.
3. **Recluster:** drop the old clusters of dirty records (their members become
   pending, since a cluster may have split). BFS the new component of each
   pending record, drop any intact old clusters pulled in by a merge, and run
   §4.5 on that component only.

Why that covers everything: when an edge or vertex is removed, every vertex of
the old component is still connected in the new G to some dirty endpoint (take
the shortest old path to the removed element; its prefix is unchanged). And
old-cluster expansion covers splits within a component.

Within one delta, records are processed in sorted order and edges are always
(re)scored against the *current* values of both endpoints. Each edge's final
value is therefore set when the later of its endpoints is processed.

**Feeding deltas from the CRDT.** `observe_deep` on `records` and `observe` on
the link maps record which keys were touched. After each transaction (local
edit or `receive`), the replica re-reads the **current** value of those keys
and passes them to `apply`. It never trusts the event payloads' values, only
their keys.

**Event caveat found during testing:** with yrs, when updates arrive out of
causal order, (a) a buffered update that gets integrated later produces **no**
observer event, and (b) a remote delete can arrive as an event with no keys.
So:
* `receive(update)` (the default) assumes causal delivery. `Replica.sync()`
  (state-vector anti-entropy) and the standard y-protocols sync provide it.
* `receive(update, ordered=False)` is for arbitrary transports: after applying
  the update, the replica diffs the whole document against the resolver's
  inputs (an O(n) scan). Re-clustering stays local.

### Data structures

| Structure | Purpose | Ops |
|---|---|---|
| `pycrdt.Doc` with nested `Map`s | replicated state | Yjs update/merge |
| `MinHashLSH` + `dict[rid, MinHash]` | fuzzy blocking | insert / remove / query |
| `dict[key, set[rid]]` | exact-key blocking | O(1) postings |
| `match_adj: dict[rid, dict[rid, score]]` | scored part of G | symmetric adjacency |
| `must_adj`, `cannot_adj: dict[rid, set[rid]]` | evidence | O(1) |
| union-find + member sets | per-component clustering | near-linear in edges |
| `cluster_of`, `clusters` | materialized view | O(1) lookups |

### Complexity (per change)
Blocking query + scoring of k candidates + BFS and clustering of the affected
component C: roughly `O(k + |E_C| log |E_C|)`. The demo measures about **0.9 ms
per incremental edit vs ~1.2 s for a full batch** over 2,000 records.

## 6. Assumptions

1. **Identical config and library versions on all replicas** (enforced via the
   config fingerprint; library versions are not enforced).
2. **Globally unique record ids**, minted by the owning source system.
   Concurrent creation of the same id is resolved LWW, for the whole record.
3. Field values are strings, and ids do not contain `\x1f`.
4. Yjs client ids are unique per replica (they order concurrent writes).
5. Event-driven incremental path: causal delivery (see §5); otherwise use
   `ordered=False`.
6. Components of G stay reasonably small (true for typical ER blocking). The
   largest component size is tracked in `resolver.stats["max_component"]`.
7. Floating-point scoring gives identical results on all replicas once rounded.

## 7. Alternatives considered

| Option | Why not (for this prototype) |
|---|---|
| Hand-rolled LWW / OR-Set CRDTs | Easy to write, but Yjs already provides tested merge, delta sync and out-of-order buffering. |
| Automerge | Also viable; pycrdt's Python bindings and sync protocol are more mature. |
| Merge-only partition-lattice CRDT | Cannot split, so it cannot handle edits, deletions or negative feedback. |
| LWW register for evidence (one key per pair) | Silently drops one side of a concurrent disagreement; separate must/cannot maps keep both and surface the conflict. |
| Fully dynamic connectivity (Holm–de Lichtenberg–Thorup) | Polylog updates instead of BFS of the component, but much more complex. Worth it only if components get big. |
| Correlation clustering (KwikCluster with hashed pivot order) | Also deterministic and local, and less prone to chaining than greedy single-linkage. A drop-in replacement for §4.5. |

## 8. Testing strategy (`tests/`)

* **Incremental == batch, always.** Hypothesis generates random interleavings
  of upserts, deletes, field deletions, match / non-match / retract, and syncs
  across 3 replicas. After **every** step, each touched replica's view is
  compared to `IncrementalResolver.batch(current_state)`. Also run with 1,500
  examples (`CONVERGENCE_EXAMPLES=1500`).
* **Convergence.** After random full anti-entropy, all replicas have identical
  partitions, golden records and conflicts, and a late joiner bootstrapping from
  one snapshot agrees.
* **Delivery order and duplication.** Each op is shipped as its own update,
  delivered in causal order with duplicates (event path) and fully shuffled
  with duplicates (`ordered=False` path).
* **Merge laws.** Every permutation of three replicas' updates, plus repeated
  application, yields identical state and view (commutativity, associativity,
  idempotence).
* **Determinism across processes.** Same output under different
  `PYTHONHASHSEED`s.
* **Semantics.** Fuzzy match, split on edit, must-link transitivity and
  retraction, cannot-link blocking a transitive chain, a concurrent
  match-vs-non-match conflict, per-field merge, config mismatch, and locality
  of recomputation.

## 9. Known limitations and next steps

* **Entity ids aren't stable**: the id is the smallest member, so it changes
  when that record leaves or a merge brings in a smaller id. A production
  system would add a deterministic id-lineage mapping (e.g. keep the id of the
  largest overlapping predecessor cluster) for downstream consumers.
* **Giant components.** Greedy single-linkage can chain. Mitigate with
  stricter blocking, correlation clustering, or a cap that falls back to
  cannot-link-style splits.
* **Config changes** require a full rebuild on every replica. Versioned configs
  (and replicating the active version) would make this an explicit,
  convergent event.
* **Float determinism** across architectures is assumed. Fixed-point scoring
  would make it exact.
* **Tombstones** accumulate in the Yjs doc (Yjs garbage-collects deleted
  content, but not the metadata of deleted items).
* **Learned matchers**: any pure, deterministic `score(a, b)` can replace §4.3.
  An ML model qualifies if its weights are pinned in the config and inference
  is deterministic.
* No persistence or network layer: `changes_since` / `receive` return and
  accept raw Yjs update bytes, ready for y-websocket, a message bus, or files.
