"""Entity resolution: blocking -> pairwise scoring -> constrained clustering.

* Blocking  : records share a block if they share an identifier, a pair of phonetic name
              tokens, or (short company names) a rare token. Oversized blocks are dropped (they add noise
              and quadratic cost while carrying little signal).
* Scoring   : explainable weighted features (name similarity, identifiers, birth date,
              countries). Each candidate pair stores its features so the UI can show *why*.
* Clustering: union-find over pairs above the auto-merge threshold plus analyst "positive"
              judgements, never merging two components joined by a "negative" judgement.
* Audit     : merge / unmerge decisions are appended to `judgements` with user + reason;
              clustering is always recomputed from candidates + judgements, so it is
              reproducible and an unmerge is just another judgement.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from functools import lru_cache
from itertools import combinations

import jellyfish
from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler
from sqlalchemy import text
from unidecode import unidecode

from .db import dumps, get_engine, init_db, loads, rows
from .ingest.common import now_iso

AUTO_MERGE = 0.88
REVIEW_FLOOR = 0.72
MAX_BLOCK = 60
MAX_AUTO_CLUSTER = 30  # safety valve against transitive chaining through one bad pair
RESOLVABLE = ("Person", "Company", "Vessel", "Aircraft")

# Legal-form words (English and transliterated Russian/other) carry no identity signal.
LEGAL_FORMS = {
    "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation", "co", "company", "sa", "s", "a",
    "ag", "gmbh", "bv", "nv", "plc", "llp", "lp", "fze", "fzco", "fzc", "fzllc", "ooo", "oao", "zao", "ao",
    "pao", "jsc", "ojsc", "cjsc", "pjsc", "sarl", "srl", "spa", "as", "ab", "oy", "kft", "doo", "sro",
    "pte", "pty", "bhd", "sdn", "the", "of", "and", "tov", "too", "dmcc", "establishment", "est",
    "liability", "joint", "stock", "closed", "open", "public", "private", "type", "obshchestvo", "obschestvo",
    "ogranichennoi", "ogranichennoy", "ogranichennoj", "otvetstvennostiu", "otvetstvennostyu",
    "otvetstvennostju", "aktsionernoe", "aktsionernoye", "zakrytoe", "otkrytoe", "publichnoe", "nepublichnoe",
    "unitary", "state", "federal", "sue", "fsue", "gup", "fgup", "tovarystvo", "z", "obmezhenoiu",
    "vidpovidalnistiu", "sociedad", "anonima", "limitada", "de", "cv", "sl", "ltda", "eirl", "sas", "bsc", "wll",
}
TITLES = {"mr", "mrs", "ms", "miss", "dr", "sir", "dame", "lord", "lady", "prof", "sheikh", "haji"}
STOP_TOKENS = {"holdings", "holding", "trading", "investments", "international", "group", "services", "general",
               "capital", "shipping", "resources", "ventures", "global", "industrial", "industries", "bank",
               "logistics", "maritime", "energy", "commodities", "consulting", "management", "enterprises",
               "enterprise", "plant", "factory", "research", "institute", "scientific", "production",
               "design", "bureau", "center", "centre", "technologies", "technology", "engineering", "systems"}


@lru_cache(maxsize=200_000)
def normalize(name: str, schema: str = "Company") -> str:
    s = unidecode(name or "").lower()
    s = re.sub(r"[^\w\s]", " ", s).replace("_", " ")
    toks = [t for t in s.split() if t]
    drop = TITLES if schema == "Person" else LEGAL_FORMS
    kept = [t for t in toks if t not in drop]
    return " ".join(kept or toks)


def tokens(name: str, schema: str) -> list[str]:
    return [t for t in normalize(name, schema).split() if len(t) >= 2 and not t.isdigit()]


@lru_cache(maxsize=200_000)
def _metaphone(t: str) -> str:
    return jellyfish.metaphone(t) or t


def block_keys(rec: dict) -> set[str]:
    """Identifier keys, plus *pairs* of phonetic tokens (two names must share two sounds),
    plus single rare tokens for one- or two-word company / vessel names."""
    keys = {f"id:{i}" for i in rec["identifiers"] if not i.startswith("icij-internal:")}
    family = "P" if rec["schema"] == "Person" else ("V" if rec["schema"] in ("Vessel", "Aircraft") else "C")
    for name in rec["names"][:8]:
        toks = [t for t in tokens(name, rec["schema"]) if t not in STOP_TOKENS and len(t) >= 3]
        codes = sorted({_metaphone(t) for t in toks})[:6]
        for x, y in combinations(codes, 2):
            keys.add(f"{family}p:{x}|{y}")
        if family != "P" and len(toks) <= 2:
            for t in toks:
                keys.add(f"{family}t:{t}")
    return keys


def _dates_compatible(a: str | None, b: str | None) -> float:
    """1 if equal at shared precision, 0 if unknown, -1 if contradictory."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if a[:n] == b[:n]:
        return 1.0
    if a[:4] == b[:4]:
        return 0.3
    return -1.0


def _token_alignment(x: list[str], y: list[str]) -> tuple[int, float, float]:
    """Greedy one-to-one token matching tolerant to typos / transliteration.
    Returns (tokens matched, coverage of the shorter name, coverage of the longer name)."""
    if not x or not y:
        return 0, 0.0, 0.0
    used: set[int] = set()
    matched, n = 0.0, 0
    for t in x:
        best, best_j = 0.0, None
        for j, u in enumerate(y):
            if j in used:
                continue
            if len(t) <= 3 or len(u) <= 3:
                # short tokens and initials: exact, an initial matching a full token,
                # or a one-letter transliteration variant of a 3-letter token (lev / lew)
                if t == u or (len(t) == 1 and u.startswith(t)) or (len(u) == 1 and t.startswith(u)):
                    sim = 1.0
                elif len(t) == len(u) == 3 and t[:2] == u[:2]:
                    sim = 0.9
                else:
                    sim = 0.0
            else:
                sim = 1.0 if t == u else JaroWinkler.similarity(t, u)
            if sim > best:
                best, best_j = sim, j
        if best_j is not None and best >= 0.88:
            used.add(best_j)
            matched += best
            n += 1
    return n, matched / min(len(x), len(y)), matched / max(len(x), len(y))


@lru_cache(maxsize=500_000)
def name_similarity(x: str, y: str, schema: str) -> float:
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    # Cheap C-level pre-filter: names sharing almost nothing never need token alignment.
    if fuzz.token_set_ratio(x, y) < 55:
        return 0.0
    tx, ty = x.split()[:10], y.split()[:10]
    # Digits are identity-bearing ("Arsenal 5" is not "Arsenal 60").
    if [t for t in tx if t.isdigit()] != [t for t in ty if t.isdigit()]:
        return 0.0
    tx, ty = [t for t in tx if not t.isdigit()], [t for t in ty if not t.isdigit()]
    n, cov_short, cov_long = _token_alignment(tx, ty)
    ts = fuzz.token_sort_ratio(x, y) / 100
    if schema == "Person":
        # Missing middle names / patronymics are common, so the shorter name's coverage
        # dominates, but only once at least two tokens line up.
        align = 0.7 * cov_short + 0.3 * cov_long if n >= 2 else 0.5 * cov_long
        return 0.75 * align + 0.25 * ts
    # Organisations and vessels: every distinctive token should be accounted for.
    return 0.7 * cov_long + 0.3 * ts


def _strong_ids(rec: dict, id_freq: dict[str, int] | None) -> set[str]:
    """Identifiers that actually identify: shared by at most 3 records (e.g. Russian KPP
    tax-office codes are shared by thousands of companies and must not count)."""
    ids = {i for i in rec["identifiers"] if not i.startswith("icij-internal:")}
    if id_freq is None:
        return ids
    return {i for i in ids if id_freq.get(i, 0) <= 3}


def score_pair(a: dict, b: dict, id_freq: dict[str, int] | None = None) -> tuple[float, dict]:
    if a["schema"] != b["schema"]:
        return 0.0, {}
    schema = a["schema"]
    na = {normalize(n, schema) for n in a["names"][:8] if n}
    nb = {normalize(n, schema) for n in b["names"][:8] if n}
    name, best = 0.0, ("", "")
    for x in na:
        for y in nb:
            sim = name_similarity(x, y, schema)
            if sim > name:
                name, best = sim, (x, y)
    shared_ids = _strong_ids(a, id_freq) & _strong_ids(b, id_freq)
    ida = {i.split(":")[0]: i for i in a["identifiers"]}
    conflicting_ids = [k for k in ("imo", "passport", "lei", "mmsi") if k in ida and any(
        i.startswith(k + ":") and i != ida[k] for i in b["identifiers"])]
    dob = _dates_compatible(a.get("birth_date"), b.get("birth_date"))
    ca, cb = set(a["countries"]), set(b["countries"])
    country = 1.0 if ca & cb else (-1.0 if ca and cb else 0.0)

    s = name
    if shared_ids:
        s = max(s, 0.75) + 0.25
    if conflicting_ids:
        s -= 0.4
    s += {1.0: 0.08, 0.3: 0.0, 0.0: 0.0, -1.0: -0.35}[dob]
    s += 0.03 if country > 0 else (-0.05 if country < 0 else 0)
    if schema == "Person" and dob == 0 and not shared_ids:
        s -= 0.04  # people with no corroborating attribute need a stronger name match
    s = max(0.0, min(1.0, s))
    return round(s, 4), {"name": round(name, 3), "names_compared": list(best), "shared_ids": sorted(shared_ids),
                         "conflicting_ids": conflicting_ids, "birth_date": dob, "country": country}


def _load_records(conn) -> list[dict]:
    out = []
    for r in rows(conn, f"SELECT id, schema, names, countries, identifiers, birth_date FROM records "
                        f"WHERE schema IN {tuple(RESOLVABLE)}"):
        r["names"] = loads(r["names"], [])
        r["countries"] = loads(r["countries"], [])
        r["identifiers"] = loads(r["identifiers"], [])
        out.append(r)
    return out


def generate_candidates(conn) -> dict:
    t0 = time.time()
    recs = _load_records(conn)
    by_id = {r["id"]: r for r in recs}
    blocks: dict[str, list[str]] = defaultdict(list)
    id_freq: dict[str, int] = defaultdict(int)
    for r in recs:
        for i in set(r["identifiers"]):
            id_freq[i] += 1
    for r in recs:
        for k in block_keys(r):
            if k.startswith("id:") and id_freq[k[3:]] > 3:
                continue
            blocks[k].append(r["id"])
    pairs: set[tuple[str, str]] = set()
    skipped = 0
    for key, members in blocks.items():
        if len(members) < 2:
            continue
        if len(members) > MAX_BLOCK and not key.startswith("id:"):
            skipped += 1
            continue
        for a, b in combinations(sorted(members), 2):
            pairs.add((a, b))
    conn.execute(text("DELETE FROM er_candidates"))
    batch = []
    for a, b in pairs:
        s, feats = score_pair(by_id[a], by_id[b], id_freq)
        if s >= REVIEW_FLOOR - 0.1:
            batch.append({"a": a, "b": b, "score": s, "features": dumps(feats)})
    for i in range(0, len(batch), 5000):
        conn.execute(text("INSERT INTO er_candidates (a, b, score, features) VALUES (:a, :b, :score, :features)"),
                     batch[i:i + 5000])
    return {"seconds": round(time.time() - t0, 1), "records": len(recs), "blocks": len(blocks), "oversized_blocks_skipped": skipped,
            "pairs_compared": len(pairs), "candidates_kept": len(batch)}


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}
        self.neg: dict[str, set[str]] = defaultdict(set)
        self.size: dict[str, int] = defaultdict(lambda: 1)

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def forbidden(self, ra: str, rb: str) -> bool:
        small, other = (ra, rb) if len(self.neg[ra]) <= len(self.neg[rb]) else (rb, ra)
        return any(self.find(n) == other for n in self.neg[small])

    def union(self, a: str, b: str, max_size: int | None = None) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        if self.forbidden(ra, rb):
            return False
        if max_size and self.size[ra] + self.size[rb] > max_size:
            return False
        keep, drop = (ra, rb) if ra < rb else (rb, ra)
        self.parent[drop] = keep
        self.size[keep] += self.size.pop(drop, 1)
        self.neg[keep] |= self.neg.pop(drop, set())
        return True


def latest_judgements(conn) -> dict[tuple[str, str], str]:
    """The newest decision per pair wins (so a later 'positive' undoes an earlier 'negative')."""
    out = {}
    for r in rows(conn, "SELECT a, b, judgement FROM judgements ORDER BY id"):
        out[tuple(sorted((r["a"], r["b"])))] = r["judgement"]
    return out


def cluster(conn, threshold: float = AUTO_MERGE) -> dict:
    uf = _UnionFind()
    judg = latest_judgements(conn)
    for (a, b), j in judg.items():
        if j == "negative":
            uf.neg[a].add(b)
            uf.neg[b].add(a)
    # Analyst positives first, then machine pairs strongest-first.
    for (a, b), j in judg.items():
        if j == "positive":
            uf.union(a, b)
    blocked = 0
    for r in conn.execute(text("SELECT a, b FROM er_candidates WHERE score >= :t ORDER BY score DESC"), {"t": threshold}):
        if judg.get(tuple(sorted((r[0], r[1])))) == "negative":
            continue
        if not uf.union(r[0], r[1], MAX_AUTO_CLUSTER):
            blocked += 1
    all_ids = [r[0] for r in conn.execute(text("SELECT id FROM records"))]
    mapping = [{"r": rid, "c": uf.find(rid) if rid in uf.parent else rid} for rid in all_ids]
    conn.execute(text("DELETE FROM resolution"))
    for i in range(0, len(mapping), 10000):
        conn.execute(text("INSERT INTO resolution (record_id, cluster_id) VALUES (:r, :c)"), mapping[i:i + 10000])
    rebuild_graph(conn)
    clusters = defaultdict(int)
    for m in mapping:
        clusters[m["c"]] += 1
    return {"records": len(mapping), "clusters": len(clusters),
            "merged_clusters": sum(1 for v in clusters.values() if v > 1), "merges_blocked": blocked}


def rebuild_graph(conn) -> None:
    """Materialise cluster-level edges (both directions are queried, so one row per edge)."""
    conn.execute(text("DELETE FROM gedges"))
    for direction, a, b in ((1, "ra", "rb"), (-1, "rb", "ra")):
        conn.execute(text(
            "INSERT INTO gedges (src, dst, edge_id, type, share, classification, dir) "
            f"SELECT {a}.cluster_id, {b}.cluster_id, e.id, e.type, e.share, e.classification, {direction} "
            "FROM edges e JOIN resolution ra ON ra.record_id = e.src JOIN resolution rb ON rb.record_id = e.dst "
            "WHERE ra.cluster_id <> rb.cluster_id"
        ))


def resolve_all(threshold: float = AUTO_MERGE) -> dict:
    engine = get_engine()
    init_db(engine)
    with engine.begin() as conn:
        stats = generate_candidates(conn)
        stats.update(cluster(conn, threshold))
    return stats


def judge(a: str, b: str, judgement: str, username: str, reason: str | None = None) -> dict:
    if judgement not in ("positive", "negative"):
        raise ValueError("judgement must be 'positive' or 'negative'")
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO judgements (a, b, judgement, username, reason, created_at) "
                          "VALUES (:a, :b, :j, :u, :r, :t)"),
                     {"a": a, "b": b, "j": judgement, "u": username, "r": reason, "t": now_iso()})
        return cluster(conn)


def unmerge(record_id: str, username: str, reason: str | None = None) -> dict:
    """Split one record out of its cluster: a negative judgement against every other member."""
    with get_engine().begin() as conn:
        cid = conn.execute(text("SELECT cluster_id FROM resolution WHERE record_id = :r"), {"r": record_id}).scalar()
        others = [r[0] for r in conn.execute(text("SELECT record_id FROM resolution WHERE cluster_id = :c AND record_id <> :r"),
                                             {"c": cid, "r": record_id})]
        for o in others:
            conn.execute(text("INSERT INTO judgements (a, b, judgement, username, reason, created_at) "
                              "VALUES (:a, :b, 'negative', :u, :r, :t)"),
                         {"a": record_id, "b": o, "u": username, "r": reason, "t": now_iso()})
        stats = cluster(conn)
    stats["split_from"] = len(others)
    return stats


def evaluate(conn=None) -> dict:
    """Pairwise precision / recall of current clusters against `gt_id` ground truth.

    Ground truth comes from OpenSanctions' own cross-list merges (real data) and from the
    synthetic generator. Only records that carry a gt_id are scored.
    """
    own = conn is None
    if own:
        conn = get_engine().connect()
    try:
        data = rows(conn, f"SELECT r.id, r.gt_id, r.source, res.cluster_id FROM records r "
                          f"JOIN resolution res ON res.record_id = r.id "
                          f"WHERE r.gt_id IS NOT NULL AND r.schema IN {tuple(RESOLVABLE)}")
    finally:
        if own:
            conn.close()

    def pairs(key):
        groups = defaultdict(list)
        for d in data:
            groups[d[key]].append(d["id"])
        return {tuple(sorted(p)) for g in groups.values() if len(g) > 1 for p in combinations(g, 2)}

    truth, pred = pairs("gt_id"), pairs("cluster_id")
    tp = len(truth & pred)
    precision = tp / len(pred) if pred else 1.0
    recall = tp / len(truth) if truth else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"records_scored": len(data), "true_pairs": len(truth), "predicted_pairs": len(pred), "true_positive": tp,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
