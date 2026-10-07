"""Walk-through: three offline replicas edit concurrently, then converge.

Run from the project root:  python examples/demo.py
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crdt_er import IncrementalResolver, Replica  # noqa: E402


def show(title, replica):
    print(f"\n== {title} (replica {replica.replica_id})")
    for e in replica.entities():
        f = e["fields"]
        print(f"  {e['id']:<8} {e['members']!s:<34} {f.get('name', ''):<14} {f.get('email', '')}")
    if replica.conflicts():
        print(f"  conflicts needing review: {replica.conflicts()}")


crm, support, billing = Replica(1), Replica(2), Replica(3)

# --- each system ingests its own records while partitioned ----------------
crm.upsert("crm:1", {"name": "John Smith", "email": "jsmith@acme.com", "address": "1 Main St, Springfield"})
crm.upsert("crm:2", {"name": "Jane Doe", "email": "jane@doe.org"})
support.upsert("sup:7", {"name": "Smith, Jon", "email": "JSmith@acme.com", "phone": "415-555-0100"})
support.upsert("sup:8", {"name": "John Smith", "email": "john@other.org"})  # a different John Smith
billing.upsert("bil:3", {"name": "Jon Smith", "phone": "+1 (415) 555 0100"})
billing.upsert("bil:4", {"name": "Jane Do", "email": "jane@doe.org"})
show("CRM before sync", crm)

# --- gossip: any order, any topology ---------------------------------------
support.sync(billing)
crm.sync(billing)
crm.sync(support)
show("after sync", crm)
assert crm.resolver.snapshot() == support.resolver.snapshot() == billing.resolver.snapshot()

# --- concurrent, conflicting human feedback --------------------------------
crm.assert_match("sup:8", "crm:1", author="alice")         # alice thinks they're the same
support.assert_non_match("sup:8", "crm:1", author="bob")   # bob disagrees, concurrently
billing.upsert("bil:4", {"email": "j.doe@new-mail.com"})   # edit breaks Jane's only shared key
for a, b in [(crm, support), (support, billing), (crm, billing)]:
    a.sync(b)
show("after concurrent feedback + edit (cannot-link wins, conflict surfaced)", billing)
assert crm.resolver.snapshot() == support.resolver.snapshot() == billing.resolver.snapshot()

# --- bob resolves the conflict; a record is deleted ------------------------
support.retract("sup:8", "crm:1")
support.assert_match("crm:2", "bil:4", author="bob")       # human re-links Jane
crm.remove("sup:7")
crm.sync(support); billing.sync(crm); support.sync(billing)
show("after retraction, manual link, and deleting sup:7 (the bridge between crm:1 and bil:3)", support)
assert crm.resolver.snapshot() == support.resolver.snapshot() == billing.resolver.snapshot()
print("\nall replicas converged ✔")

# --- incremental vs. batch cost --------------------------------------------
rnd = random.Random(0)
first = ["John", "Jon", "Jane", "Alice", "Bob", "Robert", "Maria", "Mario", "Wei", "Li"]
last = ["Smith", "Doe", "Wong", "Garcia", "Chen", "Patel", "Nguyen", "Kim", "Lopez", "Brown"]


def person(i):
    p = rnd.randrange(400)  # ~400 underlying people, several records each
    return {"name": f"{first[p % 10]} {last[p // 10 % 10]} {p}", "email": f"u{p}@mail.com" if rnd.random() < .6 else None}


r = Replica(7)
for i in range(2000):
    r.upsert(f"p{i}", {k: v for k, v in person(i).items() if v})
records, must, cannot = r.state()
t0 = time.perf_counter()
IncrementalResolver.batch(records, must, cannot)
batch_s = time.perf_counter() - t0
t0 = time.perf_counter()
for i in range(200):
    r.upsert(f"p{rnd.randrange(2000)}", {"email": f"u{rnd.randrange(400)}@mail.com"})
inc_s = (time.perf_counter() - t0) / 200
print(f"\n2000 records: full batch resolve {batch_s * 1000:.0f} ms, "
      f"incremental edit {inc_s * 1000:.2f} ms avg, "
      f"{len(r.entities())} entities, largest component {r.resolver.stats['max_component']}")
