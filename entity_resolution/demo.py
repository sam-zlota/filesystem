"""Walk-through of the CRDT entity-resolution prototype.

Run:  python demo.py
"""
from __future__ import annotations

from crdt_er import Replica


def show(title: str, *replicas: Replica) -> None:
    print(f"\n== {title}")
    for r in replicas:
        clusters = sorted(sorted(m) for m in r.entities().values())
        print(f"  {r.id}: {clusters}")
    views = {tuple(sorted(tuple(sorted(m)) for m in r.entities().values())) for r in replicas}
    print("  converged" if len(views) == 1 else "  DIVERGED (expected while deltas are in flight)")


def main() -> None:
    crm, billing, support = Replica("crm"), Replica("billing"), Replica("support")
    everyone = (crm, billing, support)

    def broadcast(src: Replica, delta) -> None:
        for r in everyone:
            if r is not src:
                r.merge(delta)

    # 1. Three systems each ingest an observation of the same customer.
    r1, d1 = crm.add_record({"name": "Jonathan Smith", "email": "JSmith@Example.com", "phone": "617-555-0199"})
    r2, d2 = billing.add_record({"name": "Jon Smith", "email": "jsmith@example.com"})
    r3, d3 = support.add_record({"name": "Jonathon Smith", "phone": "(617) 555-0199", "zip": "02139"})
    show("local ingest, nothing replicated yet", *everyone)

    # 2. Deltas arrive in different orders at each replica.
    crm.merge(d3); crm.merge(d2)
    billing.merge(d1); billing.merge(d3)
    support.merge(d2); support.merge(d1); support.merge(d1)  # duplicate delivery is harmless
    show("after delta exchange (reordered + duplicated)", *everyone)
    print("  explain", r1, r2, "->", crm.explain(r1, r2))
    print("  explain", r1, r3, "->", crm.explain(r1, r3))
    print("  explain", r2, r3, "->", crm.explain(r2, r3), "(joined only transitively via", r1 + ")")

    # 3. A new, unrelated person who happens to share a zip code.
    r4, d4 = support.add_record({"name": "Jane Smith", "zip": "02139"})
    broadcast(support, d4)
    show("unrelated 'Jane Smith' stays separate (name similarity below threshold)", *everyone)

    # 4. Billing corrects the email on its record -> it no longer auto-matches.
    d = billing.patch_record(r2, email="jon.smith@other.org")
    broadcast(billing, d)
    show("billing's email correction splits the cluster incrementally", *everyone)

    # 5. Concurrent curation: support links r2 back, crm unlinks the pair.
    d_link = support.link(r1, r2)
    d_unlink = crm.unlink(r1, r2)
    show("concurrent link (support) vs unlink (crm), before exchange", *everyone)
    broadcast(support, d_link)
    broadcast(crm, d_unlink)
    show("after exchange: unlink wins deterministically on every replica", *everyone)

    # 6. crm withdraws its unlink; the support link now takes effect everywhere.
    d = crm.retract_unlink(r1, r2)
    broadcast(crm, d)
    show("crm retracts its unlink -> curator link applies", *everyone)

    # 7. Deleting the bridging record splits the transitive cluster.
    d = crm.delete_record(r1)
    broadcast(crm, d)
    show("deleting the bridge record", *everyone)

    # 8. A late-joining replica catches up by full-state sync and agrees.
    analytics = Replica("analytics")
    analytics.merge(billing.snapshot())
    show("late joiner after one full-state sync", *everyone, analytics)
    for r in (*everyone, analytics):
        r.check_invariants()
    print("\nall replicas pass check_invariants() (incremental view == from-scratch resolution)")


if __name__ == "__main__":
    main()
