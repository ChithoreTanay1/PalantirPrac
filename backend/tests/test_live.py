"""Offline tests for the live parsers (no network, no API keys)."""
import io
import zipfile

from gotham.live import gdelt
from gotham.live.ais import AisFeed
from gotham.live.opensky import match_states
from gotham.live.watchlist import Watchlist


def _gdelt_row(code, root, lat="31.5", lon="34.45", url="https://example.org/a", place="Gaza, Israel"):
    row = [""] * 61
    row[0], row[6], row[16] = "1", "ISRAEL", "MILITANT"
    row[26], row[28], row[30], row[31], row[32] = code, root, "-10", "3", "1"
    row[51], row[52], row[53], row[56], row[57] = "4", place, "IS", lat, lon
    row[59], row[60] = "20261012164500", url
    return "\t".join(row)


def test_gdelt_keeps_conflict_and_sanctions_drops_rest_and_dedupes():
    lines = [
        _gdelt_row("190", "19"),                                # fighting
        _gdelt_row("193", "19"),                                # same article/place/category -> merged
        _gdelt_row("163", "16", url="https://example.org/s"),   # sanctions
        _gdelt_row("042", "04", url="https://example.org/v"),   # diplomatic visit -> dropped
        _gdelt_row("190", "19", lat="", url="https://example.org/x"),  # no coordinates -> dropped
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.export.CSV", "\n".join(lines))
    rows = gdelt.parse_export(buf.getvalue())
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["Fighting", "Sanctions / embargo"]
    fight = next(r for r in rows if r["kind"] == "Fighting")
    assert fight["detail"]["mentions"] == 6 and fight["geo_precision"] == "city"
    assert fight["ts"] == "2026-10-12T16:45:00Z"


def test_opensky_matches_airframe_and_operator_only():
    w = Watchlist(icao24={"730123": {"record_id": "os_us_ofac_sdn:ofac-1", "basis": "registration"}},
                  operators={"IRM": {"record_id": "os_us_ofac_sdn:ofac-2", "name": "Mahan Air"}},
                  captions={"os_us_ofac_sdn:ofac-1": "EP-ABC"})
    states = [
        ["730123", "XYZ1    ", "Iran", 1, 1791823114, 51.3, 35.7, 9000, False, 220, 90, 0, None, 9100, "1000", False, 0],
        ["730999", "IRM1071 ", "Iran", 1, 1791823114, 55.3, 25.2, 9000, False, 220, 90, 0, None, 9100, "1000", False, 0],
        ["4ca2ad", "RYR523V ", "Ireland", 1, 1791823114, 5.8, 45.0, 9000, False, 220, 90, 0, None, 9100, "1000", False, 0],
        ["730777", "IRMA    ", "Iran", 1, 1791823114, 55.3, 25.2, 9000, False, 220, 90, 0, None, 9100, "1000", False, 0],
        ["730888", "IRM2    ", "Iran", 1, 1791823114, None, None, 9000, False, 220, 90, 0, None, 9100, "1000", False, 0],
    ]
    out = match_states(states, w)
    assert [o["id"] for o in out] == ["adsb:730123", "adsb:730999"]
    assert out[0]["detail"]["match_basis"].startswith("airframe")
    assert out[1]["detail"]["match_basis"] == "operator IRM"


def test_ais_learns_mmsi_from_imo_and_filters():
    w = Watchlist(imo={"9253325": "os_us_ofac_sdn:ofac-51639"}, captions={"os_us_ofac_sdn:ofac-51639": "Nurkez"})
    feed = AisFeed(w)
    pos = {"MessageType": "PositionReport", "MetaData": {"MMSI": 273123456, "ShipName": "NURKEZ", "latitude": 36.5, "longitude": 22.7},
           "Message": {"PositionReport": {"Sog": 0.1, "Cog": 12, "NavigationalStatus": 1}}}
    feed.handle(pos)
    assert not feed.pending, "unknown MMSI must be ignored until linked"
    feed.handle({"MessageType": "ShipStaticData", "MetaData": {"MMSI": 273123456},
                 "Message": {"ShipStaticData": {"ImoNumber": 9253325, "Destination": "FOR ORDERS", "Name": "NURKEZ"}}})
    feed.handle(pos)
    row = feed.pending["273123456"]
    assert row["record_id"] == "os_us_ofac_sdn:ofac-51639"
    assert row["detail"]["nav_status"] == "at anchor" and row["detail"]["destination"] == "FOR ORDERS"
    feed.handle({"MessageType": "PositionReport", "MetaData": {"MMSI": 999, "latitude": 1, "longitude": 1}, "Message": {}})
    assert len(feed.pending) == 1


def test_ais_tracks_all_tankers_but_not_other_ships():
    feed = AisFeed(Watchlist(), track_tankers=True)
    for mmsi, stype in ((111, 84), (222, 70)):  # 84 = tanker, 70 = cargo
        feed.handle({"MessageType": "ShipStaticData", "MetaData": {"MMSI": mmsi},
                     "Message": {"ShipStaticData": {"Type": stype, "Name": f"S{mmsi}", "Dimension": {"A": 200, "B": 40}}}})
        feed.handle({"MessageType": "PositionReport", "MetaData": {"MMSI": mmsi, "latitude": 36.5, "longitude": 22.7},
                     "Message": {"PositionReport": {"Sog": 0.2}}})
    assert list(feed.pending) == ["111"]
    row = feed.pending["111"]
    assert row["record_id"] is None and row["detail"]["scope"] == "tanker" and row["detail"]["length_m"] == 240


def test_sts_hotspot_flag():
    from gotham.live.hotspots import near_sts
    assert near_sts(36.6, 22.7)[0].startswith("Laconian")
    assert near_sts(48.85, 2.35) is None
