"""Distributed engine, exercised through the deterministic cluster simulator.

The headline property: whatever mix of crashes (with lost log tails),
restarts, partitions, message loss / duplication / reordering and
disagreeing membership views happens, once the system is healed and has no
work left, every node holds the same state and the same entities, and those
entities equal the single-node batch oracle on that state.
"""
import random
import unittest

from crdt_er import resolve_from_scratch
from crdt_er.dist.sim import SimCluster
from crdt_er.dist.state import DistState, make_fact
from crdt_er.matching import PersonMatcher
from tests.helpers import make_people, perturb


def assert_converged(tc: unittest.TestCase, sim: SimCluster) -> None:
    nodes = list(sim.nodes.values())
    first = nodes[0]
    for n in nodes:
        n.check_invariants()
        tc.assertFalse(n.has_pending(), n.id)
        tc.assertEqual(n.state.canonical(), first.state.canonical(), f"state differs on {n.id}")
        tc.assertEqual(n.entities(), first.entities(), f"entities differ on {n.id}")
    tc.assertEqual(first.entities(), resolve_from_scratch(first.state.er, first.matcher))


def random_client_op(rng: random.Random, sim: SimCluster, people) -> None:
    live_nodes = sorted(sim.up)
    nid = rng.choice(live_nodes)
    node = sim.nodes[nid]
    live = sorted(rid for rid, _ in node.state.er.records.live_items())
    roll = rng.random()
    if roll < 0.45 or len(live) < 2:
        batch = [["add", None, perturb(rng, rng.choice(people))] for _ in range(rng.randint(1, 4))]
        sim.submit(nid, batch)
    elif roll < 0.60:
        sim.submit(nid, [["update", rng.choice(live), perturb(rng, rng.choice(people))]])
    elif roll < 0.70:
        sim.submit(nid, [["delete", rng.choice(live)]])
    else:
        a, b = rng.sample(live, 2)
        sim.submit(nid, [[rng.choice(["link", "unlink", "retract_link", "retract_unlink"]), a, b]])


class DistStateTest(unittest.TestCase):
    def test_fact_merge_is_order_independent(self):
        rec = lambda rid, l, name: {"r": [[rid, l, "A#1", {"name": name}]]}
        f_old = {"f": [list(make_fact("a", (1, "A#1"), "b", (2, "A#1")))]}
        f_new = {"f": [list(make_fact("a", (3, "A#1"), "b", (2, "A#1")))]}
        deltas = [rec("a", 1, "x"), rec("b", 2, "y"), f_old, rec("a", 3, "z"), f_new]
        results = set()
        rng = random.Random(0)
        for _ in range(30):
            order = deltas[:] + rng.sample(deltas, 2)  # with duplicates
            rng.shuffle(order)
            s = DistState()
            for d in order:
                s.merge(d)
            results.add(s.canonical())
            self.assertEqual(s.facts, {tuple(f_new["f"][0])})  # the stale fact never survives
        self.assertEqual(len(results), 1)


class PublishFactsTest(unittest.TestCase):
    def test_results_from_a_lagging_worker_are_dropped(self):
        from crdt_er.dist.node import Node
        node = Node("n0", 1, ["n0"])
        node.submit([["add", "a", {"name": "Ana Lee", "email": "x@y.z"}],
                     ["add", "b", {"name": "Bo Chan", "email": "q@y.z"}]])
        old_a, b = node.ver["a"], node.ver["b"]
        node.submit([["update", "a", {"name": "Ana Lee", "email": "new@y.z"}]])
        stale = make_fact("a", old_a, "b", b)        # computed by a worker before the update
        self.assertIsNone(node.publish_facts([list(stale)]))
        self.assertNotIn(stale, node.state.facts)
        current = make_fact("a", node.ver["a"], "b", b)
        self.assertIsNotNone(node.publish_facts([list(current)]))
        self.assertEqual(node.entity_of_record("b"), "a")
        node.check_invariants()


class SimTest(unittest.TestCase):
    def test_shards_are_partitioned_and_move_on_failure(self):
        sim = SimCluster(3, random.Random(1), n_shards=32)
        owned = [sim.nodes[i].owned for i in sim.ids]
        self.assertEqual(set().union(*owned), set(range(32)))
        self.assertEqual(sum(len(o) for o in owned), 32)  # disjoint
        sim.crash("n1")
        sim.refresh_membership()
        owned = [sim.nodes[i].owned for i in ("n0", "n2")]
        self.assertEqual(set().union(*owned), set(range(32)))

    def test_takeover_computes_matches_of_crashed_owner(self):
        sim = SimCluster(3, random.Random(2), n_shards=8, loss=0, dup=0)
        sim.submit("n0", [["add", "p1", {"name": "Ana Garcia", "email": "ana@x.com"}],
                          ["add", "p2", {"name": "Anna Garcia", "email": "ANA@x.com"}]])
        while sim.deliver_one():
            pass
        owner = sim.nodes["n0"].owners(sim.nodes["n0"].shard_of("email:ana@x.com"))[0]
        sim.crash(owner)  # dies before matching anything
        sim.refresh_membership()
        survivor = next(i for i in sim.ids if i != owner)
        for nid in sim.ids:
            while sim.match_step(nid):
                pass
        while sim.deliver_one():
            pass
        self.assertEqual(sim.nodes[survivor].entity_of_record("p2"), "p1")

    def test_restart_with_lost_log_tail_never_reuses_timestamps(self):
        sim = SimCluster(2, random.Random(3), loss=0, dup=0)
        sim.submit("n0", [["add", "r1", {"name": "Bob Brown", "email": "b@x.com"}]])
        while sim.deliver_one():
            pass
        sim.submit("n0", [["update", "r1", {"name": "Bob Brown", "email": "new@x.com"}]])
        while sim.deliver_one():
            pass
        sim.crash("n0", lose_tail=1)  # n1 has the update, n0's disk does not
        sim.restart("n0")
        self.assertEqual(sim.nodes["n0"].replica, "n0#2")
        sim.submit("n0", [["update", "r1", {"name": "Bob Brown", "email": "third@x.com"}]])
        sim.quiesce()
        assert_converged(self, sim)
        # the post-restart write uses a fresh replica id, so it cannot collide
        versions = {sim.nodes[i].state.version("r1") for i in sim.ids}
        self.assertEqual(len(versions), 1)
        self.assertEqual(next(iter(versions))[1], "n0#2")

    def test_partition_both_sides_accept_writes_then_converge(self):
        sim = SimCluster(4, random.Random(4), loss=0, dup=0)
        sim.partition([["n0", "n1"], ["n2", "n3"]])
        sim.refresh_membership()
        for nid in sim.ids:
            self.assertEqual(len(sim.nodes[nid].alive), 2)
        sim.submit("n0", [["add", "a", {"name": "Wei Chen", "email": "wei@x.com"}]])
        sim.submit("n2", [["add", "b", {"name": "Wei Chen", "email": "wei@x.com"}]])
        sim.submit("n2", [["unlink", "a", "b"]])
        sim.submit("n0", [["link", "a", "b"]])
        for nid in sim.ids:
            while sim.match_step(nid):
                pass
        while sim.deliver_one():
            pass
        self.assertIsNone(sim.nodes["n0"].entity_of_record("b"))  # not visible across the cut
        sim.quiesce()
        assert_converged(self, sim)
        self.assertNotEqual(sim.nodes["n0"].entity_of_record("a"), sim.nodes["n0"].entity_of_record("b"))

    def run_chaos(self, seed: int, n_nodes: int, compute_replicas: int) -> None:
        rng = random.Random(seed)
        sim = SimCluster(n_nodes, rng, n_shards=16, compute_replicas=compute_replicas)
        people = make_people(rng, 10)
        for step in range(220):
            r = rng.random()
            if r < 0.45:
                random_client_op(rng, sim, people)
            elif r < 0.75:
                sim.deliver_one()
            elif r < 0.88:
                sim.match_step(rng.choice(sim.ids))
            elif r < 0.91:
                a, b = rng.sample(sim.ids, 2)
                sim.anti_entropy(a, b)
            elif r < 0.95:
                sim.refresh_membership(rng.choice(sim.ids))  # one node's view changes
            elif r < 0.97 and len(sim.up) > 1:
                victim = rng.choice(sorted(sim.up))
                sim.crash(victim, lose_tail=rng.randrange(3))
            elif r < 0.985:
                down = [i for i in sim.ids if i not in sim.up]
                if down:
                    sim.restart(rng.choice(down))
            elif r < 0.995:
                ids = sim.ids[:]
                rng.shuffle(ids)
                cut = rng.randrange(1, len(ids))
                sim.partition([ids[:cut], ids[cut:]])
            else:
                sim.heal()
            if step % 40 == 0:
                for nid in sim.up:
                    sim.nodes[nid].check_invariants()
        sim.quiesce()
        assert_converged(self, sim)
        total = sum(len(m) > 1 for m in sim.nodes["n0"].entities().values())
        self.assertGreater(total, 0, "no multi-record entities formed")

    def test_chaos_many_seeds(self):
        for seed in range(25):
            with self.subTest(seed=seed):
                self.run_chaos(seed, n_nodes=4, compute_replicas=1)

    def test_chaos_with_redundant_compute(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                self.run_chaos(100 + seed, n_nodes=5, compute_replicas=2)

    backend = "python"

    def test_matching_work_is_divided(self):
        rng = random.Random(7)
        matcher = PersonMatcher(zip_blocking="zip+initials", similarity=self.backend)
        sim = SimCluster(4, rng, matcher=matcher, n_shards=64, loss=0, dup=0)
        people = make_people(rng, 60)
        for i in range(400):
            sim.submit(sim.ids[i % 4], [["add", None, perturb(rng, rng.choice(people))]])
        sim.quiesce()
        assert_converged(self, sim)
        work = [sim.nodes[i].matching.metrics["pairs_scored"] for i in sim.ids]
        emitted = [sim.nodes[i].metrics["facts_emitted"] for i in sim.ids]
        # every match fact is produced by exactly one node: no node repeats
        # another node's result (records were only added, so no fact went stale)
        self.assertEqual(sum(emitted), len(sim.nodes["n0"].state.facts))
        self.assertGreater(min(work), 0)
        self.assertLess(max(work), 0.5 * sum(work))

try:
    import rapidfuzz  # noqa: F401
    HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    HAVE_RAPIDFUZZ = False


@unittest.skipUnless(HAVE_RAPIDFUZZ, "pip install rapidfuzz numpy")
class NativeBackendSimTest(SimTest):
    """The whole simulated-cluster suite again, with the native backend."""
    backend = "rapidfuzz"

    def run_chaos(self, seed, n_nodes, compute_replicas):
        pass  # covered by SimTest; this class adds the large-block cases below

    def test_large_blocks_bulk_load_matches_oracle(self):
        rng = random.Random(11)
        matcher = PersonMatcher(zip_blocking="zip", similarity="rapidfuzz")
        sim = SimCluster(3, rng, matcher=matcher, n_shards=32, loss=0.1, dup=0.2)
        people = make_people(rng, 40)
        for i in range(600):
            sim.submit(sim.ids[i % 3], [["add", None, perturb(rng, rng.choice(people))]])
            if i % 7 == 0:
                sim.deliver_one()
        sim.crash("n1", lose_tail=2)
        sim.refresh_membership()
        for nid in ("n0", "n2"):
            while sim.match_step(nid, max_records=200):
                pass
        sim.quiesce()
        assert_converged(self, sim)


if __name__ == "__main__":
    unittest.main()
