"""Scenario tests for incremental resolution and conflict semantics."""
import unittest

from crdt_er import Replica, resolve_from_scratch


def sync(*replicas: Replica) -> None:
    for a in replicas:
        for b in replicas:
            if a is not b:
                a.merge(b.snapshot())


def same_view(*replicas: Replica):
    views = [r.entities() for r in replicas]
    assert all(v == views[0] for v in views), views
    for r in replicas:
        r.check_invariants()
    return views[0]


class SingleReplicaTest(unittest.TestCase):
    def test_automatic_match_and_entity_id_is_min_record_id(self):
        r = Replica("A")
        a, _ = r.add_record({"name": "John Smith", "email": "JOHN@x.com"})
        b, _ = r.add_record({"name": "J. Smith", "email": "john@x.com"})
        self.assertEqual(r.entity_of(a), r.entity_of(b))
        self.assertEqual(r.entity_of(a), min(a, b))
        self.assertEqual(r.explain(a, b), "edge: automatic match")
        r.check_invariants()

    def test_transitive_cluster_splits_when_bridge_is_deleted(self):
        r = Replica("A")
        a, _ = r.add_record({"name": "Ana Garcia", "email": "ana@x.com"})
        bridge, _ = r.add_record({"name": "Ana Garcia", "email": "ana@x.com", "phone": "617-555-0100"})
        c, _ = r.add_record({"name": "Anna Garcia", "phone": "(617) 555-0100"})
        self.assertEqual(len(r.entities()), 1)
        self.assertEqual(r.explain(a, c), "no edge: matcher rejected")  # only linked through the bridge
        r.delete_record(bridge)
        self.assertEqual(len(r.entities()), 2)
        self.assertNotEqual(r.entity_of(a), r.entity_of(c))
        self.assertIsNone(r.entity_of(bridge))
        r.check_invariants()

    def test_update_moves_record_between_entities(self):
        r = Replica("A")
        a, _ = r.add_record({"name": "Bob Brown", "email": "bob@x.com"})
        b, _ = r.add_record({"name": "Maria Chen", "email": "maria@x.com"})
        c, _ = r.add_record({"name": "Robert Brown", "email": "bob@x.com"})
        self.assertEqual(r.entity_of(a), r.entity_of(c))
        r.patch_record(c, email="maria@x.com")
        self.assertEqual(r.entity_of(b), r.entity_of(c))
        self.assertNotEqual(r.entity_of(a), r.entity_of(c))
        r.check_invariants()

    def test_unlink_overrides_matcher_and_retract_restores(self):
        r = Replica("A")
        a, _ = r.add_record({"name": "Wei Li", "email": "w@x.com"})
        b, _ = r.add_record({"name": "Wei Li", "email": "w@x.com"})
        self.assertEqual(r.entity_of(a), r.entity_of(b))
        r.unlink(a, b)
        self.assertNotEqual(r.entity_of(a), r.entity_of(b))
        r.retract_unlink(a, b)
        self.assertEqual(r.entity_of(a), r.entity_of(b))
        r.check_invariants()

    def test_link_forces_merge_and_unlink_beats_link(self):
        r = Replica("A")
        a, _ = r.add_record({"name": "Alice Jones", "email": "a@x.com"})
        b, _ = r.add_record({"name": "Mario Nguyen", "email": "m@x.com"})
        self.assertNotEqual(r.entity_of(a), r.entity_of(b))
        r.link(a, b)
        self.assertEqual(r.entity_of(a), r.entity_of(b))
        r.unlink(a, b)
        self.assertNotEqual(r.entity_of(a), r.entity_of(b))
        self.assertEqual(r.explain(a, b), "no edge: unlink assertion")
        r.check_invariants()

    def test_assertions_survive_record_deletion_and_resurrection(self):
        r = Replica("A")
        a, _ = r.add_record({"name": "Alice Jones", "email": "a@x.com"})
        b, _ = r.add_record({"name": "Mario Nguyen", "email": "m@x.com"})
        r.link(a, b)
        r.delete_record(b)
        self.assertEqual(r.entities(), {a: frozenset({a})})
        r.add_record({"name": "Mario Nguyen", "email": "m@x.com"}, record_id=b)
        self.assertEqual(r.entity_of(a), r.entity_of(b))  # link assertion still applies
        r.check_invariants()


class MultiReplicaTest(unittest.TestCase):
    def test_delta_delivery_is_order_and_duplicate_insensitive(self):
        a, b, c = Replica("A"), Replica("B"), Replica("C")
        deltas = []
        ra, d = a.add_record({"name": "John Smith", "email": "john@x.com"}); deltas.append(d)
        rb, d = b.add_record({"name": "Jon Smith", "email": "john@x.com"}); deltas.append(d)
        deltas.append(a.patch_record(ra, email="other@x.com"))
        deltas.append(b.link(rb, ra))
        for d in deltas:            # forward order
            c.merge(d)
        for d in reversed(deltas):  # reversed + duplicated
            a.merge(d); a.merge(d)
        for d in deltas[::2] + deltas[1::2]:
            b.merge(d)
        same_view(a, b, c)
        self.assertEqual(a.entity_of(ra), a.entity_of(rb))

    def test_concurrent_link_and_unlink_converge(self):
        a, b = Replica("A"), Replica("B")
        x, dx = a.add_record({"name": "Alice Jones", "email": "a@x.com"})
        y, dy = a.add_record({"name": "Alicia Jones", "email": "a@x.com"})
        b.merge(dx); b.merge(dy)
        da = a.link(x, y)      # concurrently...
        db = b.unlink(x, y)
        a.merge(db); b.merge(da)
        view = same_view(a, b)
        self.assertEqual(len(view), 2)  # unlink wins deterministically

    def test_concurrent_update_and_delete_resolve_by_lww(self):
        a, b = Replica("A"), Replica("B")
        x, dx = a.add_record({"name": "Bob Brown", "email": "bob@x.com"})
        y, dy = a.add_record({"name": "Robert Brown", "email": "bob@x.com"})
        b.merge(dx); b.merge(dy)
        d_upd = a.patch_record(x, email="moved@x.com")   # ts (3, "A")
        d_del = b.delete_record(x)                        # ts (3, "B") > (3, "A")
        a.merge(d_del); b.merge(d_upd)
        view = same_view(a, b)
        self.assertEqual(view, {y: frozenset({y})})
        self.assertIsNone(a.entity_of(x))

    def test_observed_remove_semantics_for_assertions(self):
        a, b, c = Replica("A"), Replica("B"), Replica("C")
        x, dx = a.add_record({"name": "Alice Jones", "email": "a@x.com"})
        y, dy = a.add_record({"name": "Mario Nguyen", "email": "m@x.com"})
        for r in (b, c):
            r.merge(dx); r.merge(dy)
        d_link = a.link(x, y)
        b.merge(d_link)
        d_retract = b.retract_link(x, y)       # B retracts the link it observed
        d_relink = c.link(x, y)                # C concurrently links again (fresh tag)
        sync(a, b, c)
        view = same_view(a, b, c)
        self.assertEqual(len(view), 1, "add-wins: concurrent re-link survives the retraction")
        self.assertEqual(d_retract.assertions.to_dict()["removed"], {("A", 3)})
        del d_relink

    def test_full_state_sync_equals_delta_sync(self):
        a, b = Replica("A"), Replica("B")
        deltas = []
        for i in range(5):
            _, d = a.add_record({"name": f"P{i} Lee", "email": f"p{i % 2}@x.com", "zip": "02139"})
            deltas.append(d)
        for d in deltas:
            b.merge(d)
        c = Replica("C")
        c.merge(a.snapshot())
        same_view(a, b, c)
        self.assertEqual(a.state, b.state)
        self.assertEqual(a.state, c.state)
        self.assertEqual(c.entities(), resolve_from_scratch(c.state, c.matcher))


if __name__ == "__main__":
    unittest.main()
