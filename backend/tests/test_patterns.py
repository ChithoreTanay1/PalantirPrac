"""Planted-pattern tests: inject known structures, check the tool surfaces them."""
from sqlalchemy import text

from gotham import graph, resolve
from gotham.ingest import common, icij

from .conftest import cluster_for_gt


def test_p1_duplicate_identity_resolved_across_three_sources(conn, world):
    gt = world["gt"]["patterns"]["P1_duplicate_identity"]
    clusters = {r[0] for r in conn.execute(text(
        "SELECT res.cluster_id FROM records r JOIN resolution res ON res.record_id = r.id WHERE r.gt_id = :g"),
        {"g": gt["gt_id"]})}
    assert len(clusters) == 1
    ent = graph.entity(conn, clusters.pop(), clearance=2)
    assert {r["source"] for r in ent["records"]} == {"synth_sanctions", "synth_icij", "synth_ch"}
    assert len(ent["records"]) == gt["expect_records"]
    assert ent["summary"]["sanctioned"]


def test_p2_ownership_chain_reaches_sanctioned_owner(conn, world):
    chain_gt = world["gt"]["patterns"]["P2_hidden_ownership"]["chain"]
    target = cluster_for_gt(conn, chain_gt[-1])
    res = graph.ownership_chain(conn, target, clearance=0, direction="up")
    reached = {n["id"] for n in res["nodes"]}
    for gt_id in chain_gt:
        assert cluster_for_gt(conn, gt_id) in reached, gt_id
    owner = cluster_for_gt(conn, chain_gt[0])
    assert any(t["node"] == owner for t in res["terminals"])
    assert any(n["sanctioned"] for n in res["nodes"] if n["id"] == owner)


def test_p2_cross_source_company_merge(conn):
    """The Panama shell appears in ICIJ and as a Companies House corporate PSC."""
    c = cluster_for_gt(conn, "GT-P2-LARCHMONT")
    sources = {r[0] for r in conn.execute(text(
        "SELECT r.source FROM resolution res JOIN records r ON r.id = res.record_id WHERE res.cluster_id = :c"), {"c": c})}
    assert sources == {"synth_icij", "synth_ch"}


def test_shortest_path_between_sanctioned_person_and_uk_company(conn, world):
    chain_gt = world["gt"]["patterns"]["P2_hidden_ownership"]["chain"]
    a, b = cluster_for_gt(conn, chain_gt[0]), cluster_for_gt(conn, "GT-P2-NORTHGATE")
    res = graph.shortest_path(conn, a, b, clearance=0)
    assert res["found"] and res["path"][0] == a and res["path"][-1] == b
    assert 1 <= res["hops"] <= 3


def test_p3_shell_ring_detected_as_cycle(conn, world):
    ring = {cluster_for_gt(conn, g) for g in world["gt"]["patterns"]["P3_shell_ring"]["gt_ids"]}
    cycles = graph.ownership_cycles(conn, clearance=0)
    assert any(set(c) == ring for c in cycles)
    up = graph.ownership_chain(conn, next(iter(ring)), clearance=0, direction="up")
    assert up["cycles"], "unwinding must flag the circular ownership"


def test_p4_synchronised_vessels(conn, world):
    p = world["gt"]["patterns"]["P4_synchronised_vessels"]
    a, b = (graph.cluster_of(conn, r) for r in p["record_ids"])
    hits = graph.co_movement(conn, a, clearance=0)
    assert hits and hits[0]["cluster"]["id"] == b
    assert hits[0]["count"] >= p["co_locations"]


def test_k_hop_grows_with_k(conn, world):
    start = cluster_for_gt(conn, "GT-P2-KESTREL")
    n1 = len(graph.k_hop(conn, start, 1, clearance=0)["nodes"])
    n2 = len(graph.k_hop(conn, start, 2, clearance=0)["nodes"])
    assert 1 < n1 < n2


def test_reingest_is_idempotent(world):
    stats = common.run("synth_icij", icij.read(world["out"] / "icij", source="synth_icij", synthetic=True))
    assert stats["inserted"] == 0 and stats["updated"] == 0 and stats["deleted"] == 0
    assert stats["unchanged"] > 0


def test_provenance_on_every_record(conn):
    missing = conn.execute(text("SELECT COUNT(*) FROM records WHERE source IS NULL OR source_doc IS NULL OR run_id IS NULL")).scalar()
    assert missing == 0
    missing_e = conn.execute(text("SELECT COUNT(*) FROM edges WHERE source_doc IS NULL OR run_id IS NULL")).scalar()
    assert missing_e == 0


def test_er_quality_on_synthetic_duplicates(world):
    m = resolve.evaluate()
    assert m["precision"] >= 0.9, m
    assert m["recall"] >= 0.8, m
