"""Scale benchmark: where do recursive CTEs stop being good enough?

    python -m gotham.bench --edges 1000000 10000000 --db bench.db

Builds a synthetic scale-free-ish graph straight into a *separate* database's `gedges`
table, then times k-hop (recursive CTE), k-hop (Python BFS, one query per level) and
bidirectional shortest path at k = 1..3 from random start nodes.
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import time

from sqlalchemy import text


def build(conn, n_edges: int, seed: int = 1) -> int:
    rng = random.Random(seed)
    n_nodes = max(1000, n_edges // 4)
    conn.execute(text("DELETE FROM gedges"))
    batch = []
    for i in range(n_edges):
        # preferential-ish attachment: low ids are hubs
        a = int(n_nodes * rng.random() ** 2)
        b = rng.randrange(n_nodes)
        eid = f"e{i}"
        batch += [{"s": f"n{a}", "d": f"n{b}", "e": eid, "dir": 1}, {"s": f"n{b}", "d": f"n{a}", "e": eid, "dir": -1}]
        if len(batch) >= 50000:
            conn.execute(text("INSERT INTO gedges (src, dst, edge_id, type, share, classification, dir) "
                              "VALUES (:s, :d, :e, 'LINKED', NULL, 0, :dir)"), batch)
            batch = []
    if batch:
        conn.execute(text("INSERT INTO gedges (src, dst, edge_id, type, share, classification, dir) "
                          "VALUES (:s, :d, :e, 'LINKED', NULL, 0, :dir)"), batch)
    return n_nodes


def khop_cte(conn, start: str, k: int, limit: int = 100000) -> int:
    return len(conn.execute(text("""
        WITH RECURSIVE walk(node, depth) AS (
            SELECT CAST(:s AS TEXT), 0
            UNION
            SELECT g.dst, w.depth + 1 FROM walk w JOIN gedges g ON g.src = w.node WHERE w.depth < :k
        ) SELECT node FROM walk GROUP BY node LIMIT :lim"""), {"s": start, "k": k, "lim": limit}).fetchall())


def khop_bfs(conn, start: str, k: int) -> int:
    seen, frontier = {start}, [start]
    for _ in range(k):
        nxt = set()
        for i in range(0, len(frontier), 900):
            chunk = frontier[i:i + 900]
            ph = ", ".join(f":p{j}" for j in range(len(chunk)))
            for (d,) in conn.execute(text(f"SELECT dst FROM gedges WHERE src IN ({ph})"),
                                     {f"p{j}": v for j, v in enumerate(chunk)}):
                if d not in seen:
                    seen.add(d)
                    nxt.add(d)
        frontier = list(nxt)
        if not frontier:
            break
    return len(seen)


def timed(fn, *args, reps=5):
    times, out = [], None
    for _ in range(reps):
        t = time.perf_counter()
        out = fn(*args)
        times.append(time.perf_counter() - t)
    return statistics.median(times) * 1000, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", type=int, nargs="+", default=[100_000, 1_000_000])
    ap.add_argument("--db", default="bench.db")
    args = ap.parse_args()
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{args.db}")
    from .db import get_engine, init_db

    engine = get_engine()
    init_db(engine)
    print(f"database: {engine.url}")
    print(f"{'edges':>11} {'k':>2} {'cte ms':>9} {'bfs ms':>9} {'reached':>9}")
    for n in args.edges:
        with engine.begin() as conn:
            t = time.perf_counter()
            n_nodes = build(conn, n)
            print(f"built {n:,} edges / {n_nodes:,} nodes in {time.perf_counter() - t:.1f}s")
        rng = random.Random(42)
        starts = [f"n{rng.randrange(n_nodes // 2, n_nodes)}" for _ in range(3)]  # non-hub starts
        with engine.connect() as conn:
            for k in (1, 2, 3):
                cte = [timed(khop_cte, conn, s, k, reps=3) for s in starts]
                bfs = [timed(khop_bfs, conn, s, k, reps=3) for s in starts]
                print(f"{n:>11,} {k:>2} {statistics.median(c[0] for c in cte):>9.1f} "
                      f"{statistics.median(b[0] for b in bfs):>9.1f} {statistics.median(b[1] for b in bfs):>9,}")


if __name__ == "__main__":
    main()
