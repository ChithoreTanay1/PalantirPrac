"""Test fixture: a fresh SQLite DB loaded with the synthetic, planted-pattern dataset only."""
import json
import os

import pytest


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gotham")
    os.environ["LIVE_ENABLED"] = "0"
    os.environ["DATABASE_URL"] = f"sqlite:///{(tmp / 'test.db').as_posix()}"
    from gotham import db, resolve, synth
    from gotham.ingest import common, companies_house, icij, opensanctions

    db.reset_engine()
    common.STAGING = tmp / "staging"
    out = tmp / "synthetic"
    synth.generate(seed=7, n_background=400, out=out)
    db.init_db()
    common.run("synth_sanctions", opensanctions.read(out / "synth_sanctions.ftm.json", "synth_sanctions", synthetic=True))
    common.run("synth_icij", icij.read(out / "icij", source="synth_icij", synthetic=True))
    common.run("synth_ch", companies_house.read(out / "ch", source="synth_ch", synthetic=True))
    common.run("synth_events", synth.read_events(out / "events.csv"))
    resolve.resolve_all()
    gt = json.loads((out / "ground_truth.json").read_text())
    yield {"tmp": tmp, "out": out, "gt": gt}
    db.reset_engine()


@pytest.fixture
def conn(world):
    from gotham.db import get_engine

    with get_engine().connect() as c:
        yield c


def cluster_for_gt(conn, gt_id):
    from sqlalchemy import text

    return conn.execute(text("SELECT res.cluster_id FROM records r JOIN resolution res ON res.record_id = r.id "
                             "WHERE r.gt_id = :g ORDER BY r.id LIMIT 1"), {"g": gt_id}).scalar()
