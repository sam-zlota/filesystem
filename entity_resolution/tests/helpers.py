"""Shared generators for the randomized tests."""
from __future__ import annotations

import random
from typing import Dict, List, Optional

from crdt_er import Network, Replica

FIRST = ["john", "jon", "jane", "alice", "alicia", "bob", "robert", "maria", "mario", "wei", "ana"]
LAST = ["smith", "smyth", "jones", "garcia", "chen", "brown", "braun", "lee", "li", "nguyen"]
ZIPS = ["02139", "02140", "10001", "94110"]


def make_people(rng: random.Random, n: int) -> List[Dict[str, str]]:
    people = []
    for i in range(n):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        people.append(
            {
                "name": f"{first.title()} {last.title()}",
                "email": f"{first}.{last}{i}@example.com",
                "phone": f"617-555-{rng.randrange(10000):04d}",
                "zip": rng.choice(ZIPS),
            }
        )
    return people


def perturb(rng: random.Random, person: Dict[str, str]) -> Dict[str, str]:
    """A noisy observation of ``person``: casing, typos, missing fields."""
    rec = dict(person)
    r = rng.random()
    if r < 0.3:
        rec["email"] = rec["email"].upper()
    elif r < 0.5:
        rec["email"] = ""  # unknown email -> must match via phone/zip + name
    if rng.random() < 0.3:
        name = rec["name"]
        if len(name) > 4:
            i = rng.randrange(1, len(name) - 1)
            rec["name"] = name[:i] + name[i + 1:]  # drop a character
    if rng.random() < 0.2:
        rec["name"] = " ".join(reversed(rec["name"].split()))
    if rng.random() < 0.2:
        rec["phone"] = f"({rec['phone'][:3]}) {rec['phone'][4:7]}-{rec['phone'][8:]}"
    if rng.random() < 0.15:
        rec["phone"] = ""
    if rng.random() < 0.1:
        rec["zip"] = ""
    return rec


def random_op(rng: random.Random, net: Network, people: List[Dict[str, str]], check: bool = True) -> None:
    """Perform one random local op at a random replica, broadcasting its delta."""
    replica: Replica = rng.choice(list(net.replicas.values()))
    live = sorted(replica.state.live_record_ids())
    roll = rng.random()
    delta = None
    if roll < 0.40 or len(live) < 2:
        _, delta = replica.add_record(perturb(rng, rng.choice(people)))
    elif roll < 0.55:
        rid = rng.choice(live)
        delta = replica.update_record(rid, perturb(rng, rng.choice(people)))
    elif roll < 0.65:
        delta = replica.delete_record(rng.choice(live))
    elif roll < 0.75:
        a, b = rng.sample(live, 2)
        delta = replica.link(a, b)
    elif roll < 0.85:
        a, b = rng.sample(live, 2)
        delta = replica.unlink(a, b)
    elif roll < 0.92:
        a, b = rng.sample(live, 2)
        delta = replica.retract_link(a, b)
    else:
        a, b = rng.sample(live, 2)
        delta = replica.retract_unlink(a, b)
    net.broadcast(replica.id, delta)
    if check:
        replica.check_invariants()


def canonical(view) -> tuple:
    """Order-independent representation of an entity view for comparisons."""
    return tuple(sorted((eid, tuple(sorted(members))) for eid, members in view.items()))
