"""
routing_sim.py - distance-vector routing: convergence and count-to-infinity.

Uses the very same DVTable class the real Router runs (acousticnet/network.py), in rounds: every
node advertises once per round, and the adverts are processed in a random order (in a real
network nobody controls who hears what first).

Experiment A "converge":  lines of 3..8 laptops, cold start: rounds until every node knows a
                          route to every other node.
Experiment B "line":      A - R - B, then B dies.  Round by round: the lowest cost at which any
                          survivor still believes B is reachable, with and without poison
                          reverse.  (A two-node loop: poison reverse fixes it.)
Experiment C "triangle":  A, B, C all hear each other, D hangs off C; D dies.  Even WITH poison
                          reverse the belief usually counts up to infinity - poison reverse only breaks
                          loops of two routers, exactly the limitation textbooks warn about.

Output: results/sim/routing_sim.csv
"""
import csv
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from acousticnet.network import DVTable, INF             # noqa: E402


def round_(tables, links, alive, rng=None):
    adverts = {n: tables[n].advert() for n in tables if n in alive}
    pairs = [(x, y) for a, b in links for x, y in ((a, b), (b, a)) if x in alive and y in alive]
    if rng is not None:
        rng.shuffle(pairs)
    changed = False
    for x, y in pairs:
        changed |= tables[y].on_advert(x, adverts[x], 0.0)
    return changed


def converge(nodes, links, alive=None, max_rounds=60):
    alive = set(nodes) if alive is None else alive
    tables = {n: DVTable(n) for n in nodes}
    for r in range(1, max_rounds + 1):
        round_(tables, links, alive)
        if all(tables[a].cost(b) < INF for a in alive for b in alive):
            return tables, r
    return tables, max_rounds


def failure(nodes, links, dead, poison, max_rounds=40, seed=None):
    """-> [(round, lowest cost any surviving laptop still believes it has to the dead one)].
    That belief climbing 3, 4, 5 ... up to 16 is count-to-infinity: the survivors keep
    learning the (stale) route from each other, each time one hop longer."""
    rng = random.Random(seed) if seed is not None else None
    tables = {n: DVTable(n, poison_reverse=poison) for n in nodes}
    for _ in range(len(nodes) + 2):
        round_(tables, links, set(nodes))
    alive = set(nodes) - {dead}
    for a, b in links:                                  # neighbours notice the death
        if dead in (a, b):
            other = b if a == dead else a
            tables[other].neighbor_down(dead, 0.0)
    series = [(0, min(tables[n].cost(dead) for n in alive))]
    for r in range(1, max_rounds + 1):
        changed = round_(tables, links, alive, rng)
        series.append((r, min(tables[n].cost(dead) for n in alive)))
        if not changed:
            break
    return series


def max_rounds_of(series):
    return series[-1][0]


def main(out_csv=None):
    out_csv = out_csv or os.path.join(ROOT, "results", "sim", "routing_sim.csv")
    rows = []
    for n in range(3, 9):
        nodes = list(range(1, n + 1))
        links = [(i, i + 1) for i in range(1, n)]
        _, r = converge(nodes, links)
        rows.append({"experiment": "converge", "topology": f"line of {n}", "poison_reverse": True,
                     "round": r, "cost": ""})
        print(f"line of {n}: converged in {r} rounds")
    topologies = {"line": ([1, 3, 2], [(1, 3), (3, 2)], 2),
                  "triangle": ([1, 2, 3, 4], [(1, 2), (2, 3), (1, 3), (3, 4)], 4)}
    for exp, (nodes, links, dead) in topologies.items():
        for poison in (True, False):
            # one representative run for the time-series plot: the slowest of 50 random orders
            runs = [failure(nodes, links, dead=dead, poison=poison, seed=k) for k in range(50)]
            worst = max(runs, key=len)
            for r, c in worst:
                rows.append({"experiment": exp, "topology": exp, "poison_reverse": poison, "round": r, "cost": c})
            counted = sum(1 for s_ in runs if any(3 <= c < INF for _, c in s_[1:]))
            settle = [next((r for r, c in s_ if c >= INF), max_rounds_of(s_)) for s_ in runs]
            rows.append({"experiment": exp + "_stats", "topology": exp, "poison_reverse": poison,
                         "round": round(sum(settle) / len(settle), 2), "cost": counted / len(runs)})
            print(f"{exp:8} poison={poison!s:5}: everyone knows it is dead after {sum(settle) / len(settle):5.1f} "
                  f"rounds on average; counted to infinity in {counted}/50 runs; slowest: {[c for _, c in worst][:16]}")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    main()
