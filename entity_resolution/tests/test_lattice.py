"""Algebraic laws of the merge (commutative, associative, idempotent)."""
import random
import unittest

from crdt_er import ERState, LWWMap, ORSet, Network, Replica
from tests.helpers import make_people, random_op


def joined(a: ERState, b: ERState) -> ERState:
    out = a.copy()
    out.merge(b)
    return out


def random_states(seed: int, n_replicas: int = 3, n_ops: int = 60):
    rng = random.Random(seed)
    replicas = [Replica(f"R{i}") for i in range(n_replicas)]
    net = Network(replicas, rng)
    people = make_people(rng, 6)
    for _ in range(n_ops):
        random_op(rng, net, people, check=False)
        if rng.random() < 0.5:
            net.deliver_one()
    return [r.snapshot() for r in replicas]


class LatticeLawsTest(unittest.TestCase):
    def test_lww_map_laws(self):
        rng = random.Random(1)
        maps = []
        for r in "ABC":
            m: LWWMap = LWWMap()
            for _ in range(20):
                m.set(f"k{rng.randrange(8)}", (rng.randrange(10), r), rng.choice([None, {"v": rng.random()}]))
            maps.append(m)
        a, b, c = maps
        ab = a.copy(); ab.merge(b)
        ba = b.copy(); ba.merge(a)
        self.assertEqual(ab, ba)
        ab_c = ab.copy(); ab_c.merge(c)
        bc = b.copy(); bc.merge(c)
        a_bc = a.copy(); a_bc.merge(bc)
        self.assertEqual(ab_c, a_bc)
        aa = a.copy(); self.assertEqual(aa.merge(a), set()); self.assertEqual(aa, a)

    def test_orset_add_wins_and_observed_remove(self):
        a: ORSet = ORSet()
        b: ORSet = ORSet()
        a.add(("A", 1), "x")
        b.merge(a)
        # B removes what it observed; concurrently A re-adds with a new tag.
        b.remove_tags(b.tags_of("x"))
        a.add(("A", 2), "x")
        a.merge(b); b.merge(a)
        self.assertEqual(a, b)
        self.assertTrue(a.contains("x"))  # add-wins
        # A remove of a never-observed tag is a no-op for liveness...
        c: ORSet = ORSet()
        self.assertEqual(c.remove_tags({("A", 2)}), set())
        # ...but still tombstones the tag when the add arrives later.
        c.merge(a)
        self.assertFalse(c.contains("x"))

    def test_erstate_merge_laws_on_random_histories(self):
        for seed in range(12):
            a, b, c = random_states(seed)
            self.assertEqual(joined(a, b), joined(b, a), f"commutativity seed={seed}")
            self.assertEqual(joined(joined(a, b), c), joined(a, joined(b, c)), f"associativity seed={seed}")
            self.assertEqual(joined(a, a), a, f"idempotence seed={seed}")
            # join is an upper bound: merging an operand into the join changes nothing
            ab = joined(a, b)
            self.assertFalse(ab.merge(a))
            self.assertFalse(ab.merge(b))


if __name__ == "__main__":
    unittest.main()
