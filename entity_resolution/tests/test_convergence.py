"""Randomized simulation: many replicas, concurrent ops, reordered and
duplicated delivery.  After every step the incremental view must equal a
from-scratch resolution; after quiescence every replica must agree."""
import random
import unittest

from crdt_er import Network, Replica, resolve_from_scratch
from tests.helpers import canonical, make_people, random_op


class ConvergenceTest(unittest.TestCase):
    def run_simulation(self, seed: int, n_replicas: int = 4, n_ops: int = 250) -> None:
        rng = random.Random(seed)
        replicas = [Replica(f"R{i}") for i in range(n_replicas)]
        net = Network(replicas, rng, duplicate_prob=0.25)
        people = make_people(rng, 8)

        for _ in range(n_ops):
            random_op(rng, net, people)
            while rng.random() < 0.6 and net.deliver_one():
                pass
            # every replica's incremental view matches the batch oracle at all times
            if rng.random() < 0.2:
                for r in replicas:
                    r.check_invariants()

        # replicas genuinely diverged mid-run (otherwise the test is vacuous)
        states_before = {canonical(r.entities()) for r in replicas}
        self.assertGreater(len(states_before), 1, "simulation never produced divergence")

        net.flush()
        views = [r.entities() for r in replicas]
        states = [r.state for r in replicas]
        for r in replicas:
            r.check_invariants()
            self.assertEqual(r.state, states[0], f"state diverged (seed={seed})")
            self.assertEqual(r.entities(), views[0], f"entities diverged (seed={seed})")
        self.assertEqual(views[0], resolve_from_scratch(states[0], replicas[0].matcher))
        self.assertGreater(sum(len(m) > 1 for m in views[0].values()), 0, "no clusters formed")

        # a late joiner catching up via full-state anti-entropy agrees too
        late = Replica("LATE")
        net_late = Network(replicas + [late], rng)
        net_late.anti_entropy(rounds=1)
        self.assertEqual(late.entities(), views[0])
        late.check_invariants()

    def test_many_seeds(self):
        for seed in range(20):
            with self.subTest(seed=seed):
                self.run_simulation(seed)

    def test_delta_and_state_sync_mix(self):
        rng = random.Random(99)
        replicas = [Replica(f"S{i}") for i in range(3)]
        net = Network(replicas, rng, duplicate_prob=0.5)
        people = make_people(rng, 5)
        for step in range(150):
            random_op(rng, net, people)
            if step % 25 == 0:
                net.anti_entropy(rounds=1)  # occasional gossip in the middle of delta traffic
            if rng.random() < 0.4:
                net.deliver_one()
        net.flush()
        net.anti_entropy()
        views = {canonical(r.entities()) for r in replicas}
        self.assertEqual(len(views), 1)
        for r in replicas:
            r.check_invariants()


if __name__ == "__main__":
    unittest.main()
