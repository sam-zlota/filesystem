"""Synthetic person records with realistic duplication and noise."""
from __future__ import annotations

import random
from typing import Dict, List, Tuple

FIRST = ("james john robert michael william david richard joseph thomas charles mary patricia "
         "jennifer linda elizabeth barbara susan jessica sarah karen nancy lisa betty margaret "
         "sandra ashley kimberly emily donna michelle carol amanda melissa deborah stephanie "
         "rebecca laura sharon cynthia kathleen amy angela shirley anna brenda pamela emma "
         "nicole helen samantha katherine christine debra rachel carolyn janet maria heather "
         "diane ruth julie olivia joyce virginia victoria kelly lauren christina joan evelyn").split()
LAST = ("smith johnson williams brown jones garcia miller davis rodriguez martinez hernandez "
        "lopez gonzalez wilson anderson thomas taylor moore jackson martin lee perez thompson "
        "white harris sanchez clark ramirez lewis robinson walker young allen king wright "
        "scott torres nguyen hill flores green adams nelson baker hall rivera campbell mitchell "
        "carter roberts gomez phillips evans turner diaz parker cruz edwards collins reyes").split()


def _typo(rng: random.Random, s: str) -> str:
    if len(s) < 5:
        return s
    i = rng.randrange(1, len(s) - 1)
    op = rng.random()
    if op < 0.4:
        return s[:i] + s[i + 1:]
    if op < 0.7:
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    return s[:i] + rng.choice("aeiou") + s[i + 1:]


def make_records(n: int, seed: int = 0, zips: int = 6, dup_rate: float = 1.5) -> List[Tuple[str, Dict[str, str]]]:
    """``n`` records drawn from about ``n / (1 + dup_rate)`` people.
    Returns ``[(record_id, attrs)]`` with globally unique record ids."""
    rng = random.Random(seed)
    people = []
    for i in range(max(1, int(n / (1 + dup_rate)))):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        people.append({
            "name": f"{first.title()} {last.title()}",
            "email": f"{first}.{last}.{i}@example.com",
            "phone": f"{rng.randrange(200, 999)}-555-{rng.randrange(10000):04d}",
            "zip": f"{2100 + rng.randrange(zips):05d}",
        })
    out = []
    for i in range(n):
        p = dict(rng.choice(people))
        if rng.random() < 0.35:
            p["email"] = p["email"].upper() if rng.random() < 0.5 else ""
        if rng.random() < 0.3:
            p["name"] = _typo(rng, p["name"])
        if rng.random() < 0.2:
            p["name"] = " ".join(reversed(p["name"].split()))
        if rng.random() < 0.2:
            p["phone"] = ""
        if rng.random() < 0.1:
            p["zip"] = ""
        out.append((f"src-{seed}-{i:07d}", p))
    return out
