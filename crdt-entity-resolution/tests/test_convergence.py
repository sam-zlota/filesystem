"""Property tests for the two guarantees the design rests on.

1. Incremental == batch: after every operation on every replica, the
   incrementally maintained view equals a from-scratch recomputation over the
   replica's current CRDT state.
2. Strong eventual consistency: replicas that have received the same set of
   updates (in any order, any number of times) expose identical entities.
"""

import itertools
import os
import random

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from crdt_er import IncrementalResolver, Replica

N_REPLICAS = 3
RIDS = [f"r{i}" for i in range(8)]
NAMES = ["John Smith", "Jon Smith", "Smith, John", "Jane Doe", "Jane Do", "J. Doe", "Alice Wong", "Alicia Wong"]
EMAILS = ["js@acme.com", "JS@acme.com ", "jane@x.org", "aw@y.net", None]
PHONES = ["+1 415 555 0100", "415-555-0100", "(212) 555-0199", None]
ADDRS = ["1 Main St, Springfield", "1 Main Street Springfield", "9 Elm Rd", None]

fields = st.fixed_dictionaries({
    "name": st.sampled_from(NAMES + [None]),
    "email": st.sampled_from(EMAILS),
    "phone": st.sampled_from(PHONES),
    "address": st.sampled_from(ADDRS),
})
replica = st.integers(0, N_REPLICAS - 1)
rid = st.sampled_from(RIDS)
pair = st.tuples(rid, rid).filter(lambda p: p[0] != p[1])

op = st.one_of(
    st.tuples(st.just("upsert"), replica, rid, fields),
    st.tuples(st.just("remove"), replica, rid),
    st.tuples(st.just("match"), replica, pair),
    st.tuples(st.just("non_match"), replica, pair),
    st.tuples(st.just("retract"), replica, pair),
    st.tuples(st.just("sync"), replica, replica),
)


def assert_incremental_equals_batch(r: Replica) -> None:
    records, must, cannot = r.state()
    batch = IncrementalResolver.batch(records, must, cannot, r.config)
    assert r.resolver.snapshot() == batch.snapshot()
    assert r.resolver.conflicts() == batch.conflicts()
    assert r.resolver.records == batch.records
    # every live record is in exactly one cluster
    assert set(r.resolver.cluster_of) == set(records)


def apply_local(r: Replica, kind: str, arg, fields=None) -> None:
    if kind == "upsert":
        r.upsert(arg, fields)
    elif kind == "remove":
        r.remove(arg)
    elif kind == "match":
        r.assert_match(*arg)
    elif kind == "non_match":
        r.assert_non_match(*arg)
    elif kind == "retract":
        r.retract(*arg)


def run(ops, replicas):
    for kind, i, *rest in ops:
        r = replicas[i]
        if kind == "sync":
            if i != rest[0]:
                r.sync(replicas[rest[0]])
                assert_incremental_equals_batch(replicas[rest[0]])
        else:
            apply_local(r, kind, *rest)
        assert_incremental_equals_batch(r)


@settings(max_examples=int(os.environ.get("CONVERGENCE_EXAMPLES", 150)), deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(op, max_size=40), st.randoms(use_true_random=False))
def test_replicas_converge_and_incremental_matches_batch(ops, rnd):
    replicas = [Replica(i + 1) for i in range(N_REPLICAS)]
    run(ops, replicas)

    # Anti-entropy in a random order until everyone has everything.
    pairs = list(itertools.permutations(range(N_REPLICAS), 2))
    rnd.shuffle(pairs)
    for _ in range(2):
        for i, j in pairs:
            replicas[i].sync(replicas[j])

    snaps = {r.resolver.snapshot() for r in replicas}
    assert len(snaps) == 1
    assert len({repr(r.entities()) for r in replicas}) == 1
    assert len({tuple(r.conflicts()) for r in replicas}) == 1
    for r in replicas:
        assert_incremental_equals_batch(r)

    # A late joiner bootstrapping from one full snapshot agrees too.
    fresh = Replica(99)
    fresh.receive(replicas[0].changes_since())
    assert fresh.resolver.snapshot() in snaps


local_op = st.one_of(
    st.tuples(st.just("upsert"), rid, fields),
    st.tuples(st.just("remove"), rid),
    st.tuples(st.just("match"), pair),
    st.tuples(st.just("non_match"), pair),
    st.tuples(st.just("retract"), pair),
)


@settings(max_examples=100, deadline=None)
@given(st.lists(local_op, min_size=1, max_size=14), st.randoms(use_true_random=False))
def test_update_delivery_order_and_duplication_do_not_matter(ops, rnd):
    """Ship each op as its own update.

    * in causal order with duplicates -> event-driven path (ordered=True)
    * shuffled with duplicates (dependencies may arrive after the updates
      that need them) -> reconcile path (ordered=False)
    """
    src = Replica(1)
    src_bootstrap = src.changes_since()  # the replica's own initial meta write
    updates = []
    for o in ops:
        before = src.state_vector()
        apply_local(src, *o)
        updates.append(src.changes_since(before))
    if len(ops) > 2:
        updates.append(src.changes_since(b"\x00"))  # full-state update, overlapping

    in_order = Replica(2)
    in_order.receive(src_bootstrap)
    for u in updates:
        for _ in range(rnd.randint(1, 2)):  # duplicates
            in_order.receive(u)
            assert_incremental_equals_batch(in_order)

    shuffled = Replica(3)
    delivery = [src_bootstrap] + updates + rnd.sample(updates, k=len(updates) // 2)
    rnd.shuffle(delivery)
    for u in delivery:
        shuffled.receive(u, ordered=False)
        assert_incremental_equals_batch(shuffled)

    for dst in (in_order, shuffled):
        assert dst.resolver.snapshot() == src.resolver.snapshot()
        assert dst.state() == src.state()


def test_merge_is_commutative_associative_idempotent():
    rnd = random.Random(7)
    base = [Replica(i) for i in (1, 2, 3)]
    for i, r in enumerate(base):
        for k in range(6):
            r.upsert(f"r{rnd.randrange(8)}", {"name": rnd.choice(NAMES), "email": rnd.choice(EMAILS)})
        r.assert_non_match("r0", f"r{i + 1}")
    u = [r.changes_since() for r in base]

    def merged(order):
        r = Replica(50)
        for idx in order:
            r.receive(u[idx])
        return r.resolver.snapshot(), r.state()

    expected = merged([0, 1, 2])
    for order in itertools.permutations(range(3)):
        assert merged(order) == expected
    assert merged([0, 1, 1, 2, 0, 2]) == expected  # idempotence
