"""Real multi-process cluster: crash, partition, restart, converge."""
import asyncio
import os
import shutil
import tempfile
import unittest

try:
    import msgpack  # noqa: F401
    HAVE_MSGPACK = True
except ImportError:  # pragma: no cover
    HAVE_MSGPACK = False


@unittest.skipUnless(HAVE_MSGPACK, "pip install msgpack to run the networked tests")
class WalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_incarnation_is_monotonic(self):
        from crdt_er.dist.wal import bump_incarnation
        self.assertEqual([bump_incarnation(self.dir) for _ in range(3)], [1, 2, 3])

    def test_torn_tail_is_truncated_and_log_stays_appendable(self):
        from crdt_er.dist.wal import Wal
        wal = Wal(self.dir)
        wal.open()
        for i in range(1, 4):
            wal.append("a#1", i, {"r": [[f"r{i}", i, "a#1", {"name": "x"}]]})
        wal.close()
        with open(wal.path, "ab") as fh:
            fh.write(b"\x00\x00\x01\x00partial")  # crash mid-frame
        self.assertEqual([seq for _, seq, _ in Wal(self.dir).replay()], [1, 2, 3])
        wal = Wal(self.dir)
        list(wal.replay())
        wal.open()
        wal.append("a#2", 1, {})
        wal.close()
        self.assertEqual([(o, s) for o, s, _ in Wal(self.dir).replay()][-1], ("a#2", 1))


@unittest.skipUnless(HAVE_MSGPACK, "pip install msgpack to run the networked tests")
class ClusterTest(unittest.TestCase):
    def test_kill_partition_restart_converges_to_oracle(self):
        import ha_demo
        out = asyncio.run(ha_demo.run(records=6000, batch=30, rate_batches_per_s=20, quiet=True))
        self.assertEqual(out["failed_batches"], 0, "a write was lost to a node failure")
        self.assertEqual(out["acked"], 6000)
        self.assertEqual(out["records"], 6000)
        self.assertEqual(out["owned_after_kill"], 64, "survivors did not take over the dead node's shards")
        self.assertTrue(out["diverged_during_partition"], "partition had no effect")
        self.assertEqual(out["n1_replica"], "n1#2")
        self.assertTrue(out["states_identical"])
        self.assertTrue(out["oracle_ok"])


if __name__ == "__main__":
    unittest.main()
