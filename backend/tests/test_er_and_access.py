"""Entity-resolution unit tests, merge/unmerge audit trail, and permission/audit tests."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from gotham import graph, resolve

from .conftest import cluster_for_gt


def rec(name, schema="Person", **kw):
    return {"id": name, "schema": schema, "names": [name], "countries": kw.get("countries", []),
            "identifiers": kw.get("identifiers", []), "birth_date": kw.get("birth_date")}


def test_normalize_strips_legal_forms_and_transliterates():
    assert resolve.normalize("OOO «Vektor»") == "vektor"
    assert resolve.normalize("Limited Liability Company Kismet Capital Group") == "kismet capital group"
    assert resolve.normalize("Сергей Иванов", "Person") == "sergei ivanov"


def test_scoring_handles_typical_variants():
    s, _ = resolve.score_pair(rec("Viktor Arkadyevich Morozan", birth_date="1968-03-14"),
                              rec("MOROZAN, Viktor", birth_date="1968-03"))
    assert s >= resolve.AUTO_MERGE
    s, _ = resolve.score_pair(rec("Lev Aurelich Drasaran"), rec("Lew Aurelich Drasaran"))
    assert s >= resolve.REVIEW_FLOOR


def test_scoring_rejects_lookalikes():
    assert resolve.score_pair(rec("Arsenal 5", "Company"), rec("Arsenal 60", "Company"))[0] < resolve.REVIEW_FLOOR
    assert resolve.score_pair(rec("Sergey Pavlovich Ivanov", birth_date="1953"),
                              rec("Sergey Borisovich Ivanov", birth_date="1961"))[0] < resolve.AUTO_MERGE
    assert resolve.score_pair(rec("Kismet Capital Group", "Company"), rec("Sberbank Capital", "Company"))[0] < resolve.REVIEW_FLOOR


def test_shared_identifier_is_decisive():
    a = rec("Velmora Star", "Vessel", identifiers=["imo:9478812"])
    b = rec("VELMORA", "Vessel", identifiers=["imo:9478812"])
    assert resolve.score_pair(a, b)[0] >= resolve.AUTO_MERGE


def test_unmerge_then_remerge_with_audit_trail(conn):
    c = cluster_for_gt(conn, "GT-P1-MOROZAN")
    members = [r[0] for r in conn.execute(text("SELECT record_id FROM resolution WHERE cluster_id = :c ORDER BY record_id"), {"c": c})]
    victim = next(m for m in members if m.startswith("synth_icij:"))
    resolve.unmerge(victim, "admin", "test split")
    with conn.engine.connect() as c2:
        assert graph.cluster_of(c2, victim) != graph.cluster_of(c2, "synth_sanctions:synth-morozan")
        assert c2.execute(text("SELECT COUNT(*) FROM judgements WHERE a = :v AND judgement = 'negative'"), {"v": victim}).scalar() >= 1
    resolve.judge(victim, "synth_sanctions:synth-morozan", "positive", "admin", "test re-merge")
    with conn.engine.connect() as c2:
        assert graph.cluster_of(c2, victim) == graph.cluster_of(c2, "synth_sanctions:synth-morozan")
        reasons = [r[0] for r in c2.execute(text("SELECT reason FROM judgements ORDER BY id"))]
    assert reasons[-1] == "test re-merge" and "test split" in reasons
    # restore the shared fixture state for other tests
    from gotham.db import get_engine
    with get_engine().begin() as c3:
        c3.execute(text("DELETE FROM judgements WHERE reason LIKE 'test %'"))
        resolve.cluster(c3)


# ------------------------------------------------------------------ permissions & audit


def client():
    from gotham.api import app

    return TestClient(app)


def test_analyst_never_sees_classified_events_or_links(conn):
    c = client()
    cid = graph.cluster_of(conn, "synth_sanctions:synth-morozan")
    low = c.post("/api/events", json={"ids": [cid]}, headers={"x-user": "analyst"}).json()
    high = c.post("/api/events", json={"ids": [cid]}, headers={"x-user": "admin"}).json()
    assert all(e["classification"] == 0 for e in low)
    assert {e["classification"] for e in high} == {0, 1, 2}
    courier = cluster_for_gt(conn, "GT-P5-COURIER")
    kestrel = cluster_for_gt(conn, "GT-P2-KESTREL")
    g = c.post("/api/graph/expand", json={"ids": [kestrel]}, headers={"x-user": "analyst"}).json()
    assert courier not in {n["id"] for n in g["nodes"]}
    g = c.post("/api/graph/expand", json={"ids": [kestrel]}, headers={"x-user": "admin"}).json()
    assert courier in {n["id"] for n in g["nodes"]}
    p = c.get("/api/graph/path", params={"a": kestrel, "b": courier}, headers={"x-user": "analyst"}).json()
    assert not p["found"]


def test_every_access_is_logged(conn):
    c = client()
    before = conn.execute(text("SELECT COUNT(*) FROM audit_log WHERE username = 'investigator'")).scalar()
    cid = graph.cluster_of(conn, "synth_sanctions:synth-morozan")
    c.get("/api/search", params={"q": "morozan"}, headers={"x-user": "investigator"})
    c.get("/api/entity", params={"id": cid}, headers={"x-user": "investigator"})
    c.get("/api/graph/khop", params={"id": cid, "k": 1}, headers={"x-user": "investigator"})
    with conn.engine.connect() as c2:
        after = c2.execute(text("SELECT COUNT(*) FROM audit_log WHERE username = 'investigator'")).scalar()
        actions = [r[0] for r in c2.execute(text("SELECT action FROM audit_log WHERE username = 'investigator' ORDER BY id DESC LIMIT 3"))]
    assert after - before == 3
    assert set(actions) == {"search", "view_entity", "k_hop"}


def test_unknown_user_rejected():
    assert client().get("/api/me", headers={"x-user": "mallory"}).status_code == 401


def test_ground_truth_never_exposed(conn):
    cid = graph.cluster_of(conn, "synth_sanctions:synth-morozan")
    ent = client().get("/api/entity", params={"id": cid}, headers={"x-user": "admin"}).json()
    assert all("gt_id" not in r for r in ent["records"])
