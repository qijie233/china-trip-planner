"""高德返回值解析测试（离线）。"""
from __future__ import annotations

import amap_client
import pytest
from fixtures import FakeAmap


def test_driving_parsing() -> None:
    amap = FakeAmap()
    r = amap.driving((31.95, 119.17), (31.968, 118.797))
    assert r is not None
    assert r["distance_km"] == pytest.approx(46.6, abs=0.05)
    assert r["duration_min"] == 49
    assert r["tolls"] == 0
    assert r["taxi_cost"] == 126.0
    assert r["traffic_lights"] == 12


def test_transit_parsing() -> None:
    plans = FakeAmap().transit((31.95, 119.17), (31.968, 118.797), "镇江", "南京")
    assert len(plans) == 2
    p = plans[0]
    assert p["duration_min"] == 115
    assert p["cost"] == 9.0
    assert p["walk_m"] == 1291 + 379 + 323
    assert p["walk_min"] >= 30
    assert p["has_subway"] is True
    kinds = [leg["kind"] for leg in p["legs"]]
    assert kinds == ["walk", "bus", "walk", "bus", "walk", "bus"]
    first_walk = p["legs"][0]
    assert first_walk["entrance"] == "2号口"
    assert "2号口" in first_walk["text"]
    bus = p["legs"][1]
    assert bus["from"] == "崇明" and bus["to"] == "马群" and bus["stops"] == 10
    assert "43 分" in bus["text"]
    last_bus = p["legs"][-1]
    assert last_bus["exit"] == "8号口"


def test_transit_rail_segment_has_times() -> None:
    plans = FakeAmap().transit((31.95, 119.17), (34.26, 117.18), "镇江", "徐州")
    rail = [leg for leg in plans[1]["legs"] if leg["kind"] == "rail"]
    assert rail, "跨城公交里应该识别出火车段"
    leg = rail[0]
    assert leg["line"] == "D126"
    assert leg["dep"] == "21:12" and leg["arr"] == "23:59"
    assert "2小时47分" in leg["text"]


def test_transit_taxi_segment() -> None:
    plans = FakeAmap().transit((31.95, 119.17), (34.26, 117.18), "镇江", "徐州")
    taxi = [leg for leg in plans[1]["legs"] if leg["kind"] == "taxi"]
    assert taxi and taxi[0]["distance_m"] == 5700


def test_empty_segments_are_skipped() -> None:
    """高德每个 segment 都带空的 walking/bus/railway/taxi 键，空的不能算一段。"""
    plans = FakeAmap().transit((31.95, 119.17), (31.968, 118.797), "镇江")
    assert plans[0]["legs"], "腿不能为空"
    for leg in plans[0]["legs"]:
        assert leg.get("text"), leg


def test_walking_parsing() -> None:
    r = FakeAmap().walking((31.95, 119.17), (31.952, 119.172))
    assert r is not None
    assert r["distance_km"] == 1.2
    assert r["duration_min"] == 16
    assert len(r["steps"]) == 2


def test_place_text_parsing() -> None:
    pois = FakeAmap().place_text("花里乡营地", "昆山")
    assert len(pois) == 2
    assert pois[0]["name"] == "花香里营地"
    assert pois[0]["address"] == "玉带北路369号"
    assert pois[0]["coord"] == (31.354666, 120.898843)


def test_walking_swallows_error() -> None:
    amap = FakeAmap(fail={"direction/walking"})
    assert amap.walking((31.95, 119.17), (31.96, 119.18)) is None


def test_transit_raises_error() -> None:
    amap = FakeAmap(fail={"direction/transit"})
    with pytest.raises(amap_client.AmapError):
        amap.transit((31.95, 119.17), (31.96, 119.18), "镇江")


def test_helpers() -> None:
    assert amap_client._num("126.0") == 126.0
    assert amap_client._num("") is None
    assert amap_client._num([]) is None
    assert amap_client._int("49.6") == 50
    assert amap_client._int("49.4") == 49
    assert amap_client._hhmm("2112") == "21:12"
    assert amap_client._hhmm("930") == "09:30"
    assert amap_client._hhmm("") == ""
    assert amap_client._fmt_span(167) == "2小时47分"
    assert amap_client._fmt_span(120) == "2小时"
    assert amap_client._fmt_span(45) == "45分"
    assert amap_client._coord_text((31.95, 119.17)) == "119.170000,31.950000"


def test_missing_key_rejected() -> None:
    with pytest.raises(amap_client.AmapError):
        amap_client.Amap("")
