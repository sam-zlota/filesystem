import os
import subprocess
import sys
import textwrap

import pytest

from crdt_er import ConfigMismatch, IncrementalResolver, Replica, ResolverConfig


def test_fuzzy_match_and_split_on_edit():
    r = Replica(1)
    r.upsert("a", {"name": "John Smith", "email": "js@acme.com"})
    r.upsert("b", {"name": "Smith, Jon", "email": "JS@acme.com"})
    assert r.entity_of("a")["members"] == ["a", "b"]
    # Dynamic: an edit that removes the shared evidence splits the entity.
    r.upsert("b", {"email": "other@else.com"})
    assert r.entity_of("a")["members"] == ["a"]


def test_same_name_alone_is_not_enough():
    r = Replica(1)
    r.upsert("a", {"name": "John Smith", "email": "a@x.com"})
    r.upsert("b", {"name": "John Smith", "email": "b@y.com"})
    assert len(r.entities()) == 2


def test_must_link_overrides_low_score_and_transitivity():
    r = Replica(1)
    r.upsert("a", {"name": "Robert Jones"})
    r.upsert("b", {"name": "Bob Jones"})
    r.upsert("c", {"name": "Bobby Jones", "phone": "212 555 0199"})
    r.upsert("d", {"name": "Bobby Jones", "phone": "(212) 555-0199"})
    r.assert_match("a", "b")
    r.assert_match("b", "c")
    assert r.entity_of("a")["members"] == ["a", "b", "c", "d"]
    r.retract("b", "c")
    assert r.entity_of("a")["members"] == ["a", "b"]
    assert r.entity_of("c")["members"] == ["c", "d"]


def test_cannot_link_blocks_transitive_chain_and_reports_conflict():
    r = Replica(1)
    r.assert_match("a", "b")
    r.assert_match("b", "c")
    r.assert_non_match("a", "c")
    for x in "abc":
        r.upsert(x, {"name": x})
    # (a,b) is processed first in canonical order, then (b,c) would put a and c together.
    assert r.entity_of("a")["members"] == ["a", "b"]
    assert r.conflicts() == [("b", "c")]


def test_concurrent_match_vs_non_match_is_conservative():
    x, y = Replica(1), Replica(2)
    x.upsert("a", {"name": "Ann Lee", "email": "ann@lee.io"})
    x.sync(y)
    y.upsert("b", {"name": "Anne Lee", "email": "ann@lee.io"})
    y.sync(x)
    assert x.entity_of("a")["members"] == ["a", "b"]
    x.assert_match("a", "b", author="alice")       # concurrently...
    y.assert_non_match("a", "b", author="bob")     # ...disagree
    x.sync(y)
    for r in (x, y):
        assert r.entity_of("a")["members"] == ["a"]  # cannot-link wins
        assert r.conflicts() == [("a", "b")]         # and is surfaced


def test_concurrent_field_edits_merge_per_field():
    x, y = Replica(1), Replica(2)
    x.upsert("a", {"name": "Ann Lee"})
    x.sync(y)
    x.upsert("a", {"email": "ann@lee.io"})
    y.upsert("a", {"phone": "415 555 0100"})
    x.sync(y)
    assert x.state()[0]["a"] == {"name": "Ann Lee", "email": "ann@lee.io", "phone": "415 555 0100"}
    assert x.state() == y.state()


def test_config_mismatch_detected():
    x = Replica(1)
    y = Replica(2, ResolverConfig(match_threshold=3.0))
    with pytest.raises(ConfigMismatch):
        x.receive(y.changes_since())


def test_incremental_only_touches_affected_component():
    r = Replica(1)
    for i in range(30):
        r.upsert(f"p{i}", {"name": f"Person{i} Zz{i * 7919 % 1000}", "email": f"p{i}@x.com"})
    before = r.resolver.stats["components_recomputed"]
    touched = r.upsert("q", {"name": "Someone Else", "email": "p3@x.com"})
    assert touched == {"q", "p3"} or touched == {"q"}
    assert r.resolver.stats["components_recomputed"] - before <= 2


def test_view_is_independent_of_python_hash_seed():
    """Replicas run in different processes; nothing may depend on hash()."""
    script = textwrap.dedent("""
        from crdt_er import IncrementalResolver
        names = ["John Smith", "Jon Smith", "Smith, John", "Jane Doe", "Jane Do", "Alice Wong"]
        recs = {f"r{i}": {"name": names[i % 6], "email": ["a@x", "b@y"][i % 2]} for i in range(24)}
        r = IncrementalResolver.batch(recs, [("r0", "r5")], [("r1", "r7")])
        print(sorted(sorted(c) for c in r.snapshot()))
    """)
    outs = set()
    for seed in ("1", "2", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        cwd = os.path.dirname(os.path.dirname(__file__))
        outs.add(subprocess.run([sys.executable, "-c", script], env=env, cwd=cwd,
                                capture_output=True, text=True, check=True).stdout)
    assert len(outs) == 1
