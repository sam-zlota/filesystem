"""Matcher backends: the native fast paths must decide exactly like the reference."""
import random
import unittest

from crdt_er.dist.workload import make_records
from crdt_er.matching import PersonMatcher, jaro_winkler

try:
    from rapidfuzz.distance import JaroWinkler
    HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    HAVE_RAPIDFUZZ = False


@unittest.skipUnless(HAVE_RAPIDFUZZ, "pip install rapidfuzz numpy")
class BackendTest(unittest.TestCase):
    def test_python_jaro_winkler_equals_rapidfuzz(self):
        names = [PersonMatcher().normalize(a).data[0] for _, a in make_records(2000, seed=4)]
        rng = random.Random(0)
        for _ in range(20000):
            a, b = rng.choice(names), rng.choice(names)
            self.assertAlmostEqual(jaro_winkler(a, b), JaroWinkler.similarity(a, b), places=12)

    def test_backends_take_identical_decisions(self):
        py = PersonMatcher(zip_blocking="zip", similarity="python")
        rf = PersonMatcher(zip_blocking="zip", similarity="rapidfuzz")
        norms = [py.normalize(a) for _, a in make_records(1500, seed=6, zips=2)]
        rng = random.Random(1)
        for _ in range(30000):
            a, b = rng.choice(norms), rng.choice(norms)
            self.assertEqual(py.match(a, b), rf.match(a, b))

    def test_match_block_equals_pairwise_except_pairs_owned_by_a_smaller_key(self):
        m = PersonMatcher(zip_blocking="zip", similarity="rapidfuzz")
        norms = [m.normalize(a) for _, a in make_records(3000, seed=8, zips=2)]
        blocks = {}
        for n in norms:
            for k in n.keys:
                blocks.setdefault(k, []).append(n)
        big = [(k, v) for k, v in blocks.items() if len(v) >= 40]
        self.assertTrue(big, "need large blocks to exercise the native path")
        for k, members in big:
            queries = members[: len(members) // 2]
            got = set(m.match_block(k, queries, members))
            want = {(i, j) for i, a in enumerate(queries) for j, b in enumerate(members) if m.match(a, b)}
            # a pair sharing a key that sorts before k is that key's job: match_block may omit it
            owned_elsewhere = {(i, j) for (i, j) in want if min(queries[i].keys & members[j].keys) < k}
            self.assertTrue(got <= want, "match_block returned a non-match")
            self.assertEqual(want - got, (want - got) & owned_elsewhere)
            self.assertEqual(got - owned_elsewhere, want - owned_elsewhere)


    def test_match_block_on_large_phone_and_email_blocks(self):
        """Blocks whose own rule is not the zip rule: a shared phone needs only
        0.80 name similarity, a shared email needs none at all."""
        m = PersonMatcher(similarity="rapidfuzz")
        variants = ["Jonathan Smith", "Jonathon Smyth", "Jon Smith", "John Smithe", "Jonathan Smit",
                    "J Smith", "Jonny Smith", "Smith Jonathan", "Joanna Smith", "Jonas Smitt",
                    "Nathan Smith", "Jon Smyth", "Johan Smith", "Jonathan Smithson"]
        phone_block = [m.normalize({"name": n, "phone": "617-555-0100"}) for n in variants]
        email_block = [m.normalize({"name": n, "email": "shared@x.com"}) for n in variants + ["Zed Q", "Ann Bo"]]
        for key, block in (("phone:6175550100", phone_block), ("email:shared@x.com", email_block)):
            self.assertGreaterEqual(len(block) ** 2, 64)  # large enough for the native path
            want = {(i, j) for i, a in enumerate(block) for j, b in enumerate(block) if m.match(a, b)}
            self.assertEqual(set(m.match_block(key, block, block)), want, key)
        # the phone block really contains pairs between the two thresholds
        sims = [JaroWinkler.similarity(a.data[0], b.data[0]) for a in phone_block for b in phone_block]
        self.assertTrue(any(0.80 <= x < 0.93 for x in sims))


if __name__ == "__main__":
    unittest.main()
