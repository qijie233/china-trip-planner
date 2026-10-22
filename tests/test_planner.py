"""trip_planner 的纯逻辑测试（离线，不联网、不查 12306）。"""
from __future__ import annotations

import copy
import json

import pytest
import trip_planner as tp
from fixtures import DRIVING, Args, FakeAmap


@pytest.fixture(scope="module")
def costs() -> dict:
    return tp._load_json(tp.SKILL_DIR / "references" / "costs.json")


# --- 基础解析 --------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("31.95,119.17", (31.95, 119.17)),
    ("31.95，119.17", (31.95, 119.17)),
    (" 31.95 119.17 ", (31.95, 119.17)),
    ("31.95", None),
    ("abc,def", None),
    ("91,119", None),
    ("", None),
])
def test_parse_coord(text, expected) -> None:
    assert tp._parse_coord(text) == expected


def test_parse_money_and_duration() -> None:
    assert tp._price_num("¥103.0") == 103.0
    assert tp._price_num(17) == 17.0
    assert tp._price_num(None) is None
    assert tp._duration_min("01:10") == 70
    assert tp._duration_min("2小时") is None
    assert tp._fmt_min(115) == "1小时55分"
    assert tp._fmt_min(120) == "2小时"
    assert tp._fmt_min(45) == "45分"
    assert tp._fmt_money(None) == "—"


def test_haversine() -> None:
    # 句容市区 → 南京南站直线约 35 km
    d = tp.haversine_km((31.95, 119.17), (31.968, 118.797))
    assert 33 < d < 37


def test_estimate_distance_speed_tiers(costs: dict) -> None:
    # 直线 10 km → 14 km（绕行 1.4），市区速度 26 km/h
    km, minutes = tp.estimate_distance(costs, 10)
    assert km == pytest.approx(14.0)
    assert minutes == pytest.approx(round(14 / 26 * 60))
    # 直线 60 km → 84 km，城际 55 km/h
    km, minutes = tp.estimate_distance(costs, 60)
    assert minutes == pytest.approx(round(84 / 55 * 60))
    # 直线 300 km → 420 km，长途 80 km/h
    km, minutes = tp.estimate_distance(costs, 300)
    assert minutes == pytest.approx(round(420 / 80 * 60))


def test_taxi_fare(costs: dict) -> None:
    _, rule = tp.pick_city_rule(costs, "南京")
    assert tp.taxi_fare(rule, 1) == 11.0  # 起步价兜底
    assert tp.taxi_fare(rule, 10) == pytest.approx(11 + 7 * 2.4)


def test_drive_cost_with_real_tolls(costs: dict) -> None:
    plain = tp.drive_cost(costs, 100)
    assert plain == pytest.approx(0.6 * 100 + 0.5 * 100 * 0.8)
    with_tolls = tp.drive_cost(costs, 100, tolls=86)
    assert with_tolls == pytest.approx(60 + 86)


def test_pick_city_rule(costs: dict) -> None:
    assert tp.pick_city_rule(costs, "南京")[0] == "南京"
    assert tp.pick_city_rule(costs, "南京市雨花台区")[0] == "南京"
    assert tp.pick_city_rule(costs, "火星")[0] == "默认"
    assert tp.pick_city_rule(costs, None)[0] == "默认"


# --- 排序 ------------------------------------------------------------------
def _entries() -> list[dict]:
    return [
        {"name": "自驾", "total_min": 55, "cost": 28.0},
        {"name": "高铁", "total_min": 35, "cost": 32.0},
        {"name": "公交", "total_min": 115, "cost": 9.0},
    ]


@pytest.mark.parametrize("prefer,expected", [
    ("time", "高铁"),
    ("price", "公交"),
    ("balance", "高铁"),
])
def test_rank_entries(prefer, expected) -> None:
    ordered = tp.rank_entries(_entries(), prefer)
    assert ordered[0]["name"] == expected
    assert ordered[0]["time_rank"] == 0 or ordered[0]["cost_rank"] == 0


def test_build_trends_marks_one_recommended() -> None:
    ordered = tp.build_trends(_entries(), "balance")
    assert sum(1 for e in ordered if e["recommended"]) == 1
    assert ordered[0]["recommended"] is True


def test_select_rail_dedupes_same_dep_arr() -> None:
    options = [
        {"dep": "13:35", "arr": "15:20", "total_min": 105, "cost": 106.0, "legs": ["A1"]},
        {"dep": "13:35", "arr": "15:20", "total_min": 105, "cost": 112.0, "legs": ["A2"]},
        {"dep": "09:00", "arr": "10:10", "total_min": 70, "cost": 103.0, "legs": ["A3"]},
    ]
    picked = tp.select_rail_options(options, "time", limit=5)
    assert len(picked) == 2
    assert {p["dep"] for p in picked} == {"13:35", "09:00"}
    same_slot = next(p for p in picked if p["dep"] == "13:35")
    assert same_slot["cost"] == 106.0, "同一时刻应留最便宜的"


def test_select_rail_prefers_priced() -> None:
    options = [
        {"dep": "07:00", "arr": "08:00", "total_min": 60, "cost": None},
        {"dep": "09:00", "arr": "10:30", "total_min": 90, "cost": 100.0},
    ]
    picked = tp.select_rail_options(options, "time", limit=2)
    assert [p["cost"] for p in picked] == [100.0]


def test_pick_transit_plan() -> None:
    plans = [
        {"duration_min": 60, "cost": 6.0},
        {"duration_min": 90, "cost": 2.0},
    ]
    assert tp.pick_transit_plan(plans, "time")["cost"] == 6.0
    assert tp.pick_transit_plan(plans, "price")["cost"] == 2.0
    assert tp.pick_transit_plan([], "balance") is None


# --- 铁路结果归一化 --------------------------------------------------------
def test_normalize_rail() -> None:
    rail = {
        "ok": True,
        "data": {
            "direct": [{"code": "G7289", "from_name": "句容", "to_name": "太仓",
                        "dep": "09:00", "arr": "10:10", "lishi": "01:10",
                        "selected_price": "103.0"}],
            "plans": [{
                "total_min": 132, "total_price": "132.0", "transfers": [{}],
                "legs": [
                    {"code": "D3076", "from_name": "句容", "to_name": "南京南",
                     "dep": "07:38", "arr": "07:58"},
                    {"code": "G7507", "from_name": "南京南", "to_name": "昆山南",
                     "dep": "08:24", "arr": "09:50"},
                ],
            }],
        },
    }
    options = tp.normalize_rail(rail)
    assert len(options) == 2
    direct, transfer = options
    assert direct["kind"] == "direct" and direct["total_min"] == 70
    assert direct["from_station"] == "句容" and direct["to_station"] == "太仓"
    assert transfer["kind"] == "transfer" and transfer["legs"] == ["D3076", "G7507"]
    assert transfer["from_station"] == "句容" and transfer["to_station"] == "昆山南"


def test_normalize_rail_ignores_failed_call() -> None:
    assert tp.normalize_rail({"ok": False}) == []


# --- Key 解析 --------------------------------------------------------------
def test_resolve_amap_key_precedence(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AMAP_KEY", raising=False)
    monkeypatch.setattr(tp, "SKILL_DIR", tmp_path)
    assert tp.resolve_amap_key(Args(amap_key="CLI-KEY")) == ("CLI-KEY", "命令行 --amap-key")
    monkeypatch.setenv("AMAP_KEY", "ENV-KEY")
    assert tp.resolve_amap_key(Args()) == ("ENV-KEY", "环境变量 AMAP_KEY")
    monkeypatch.delenv("AMAP_KEY")
    ref = tmp_path / "references"
    ref.mkdir()
    (ref / "amap.json").write_text(json.dumps({"key": "FILE-KEY"}), encoding="utf-8")
    assert tp.resolve_amap_key(Args()) == ("FILE-KEY", "references/amap.json")
    assert tp.resolve_amap_key(Args(amap_key="CLI-KEY"))[0] == "CLI-KEY"


# --- 接驳 ------------------------------------------------------------------
ACCESS_TRANSIT = {
    "status": "1", "info": "OK",
    "route": {"transits": [{
        "cost": "2.0", "duration": "3600", "walking_distance": "300",
        "distance": "4000",
        "segments": [
            {"walking": {"distance": "300", "duration": "240", "steps": []}},
            {"bus": {"buslines": [{"name": "句容1路", "type": "普通公交",
                                   "distance": "3700", "duration": "3360",
                                   "departure_stop": {"name": "建材市场"},
                                   "arrival_stop": {"name": "句容站"}, "via_num": "9"}]}},
        ],
    }]},
}

ACCESS_DRIVING = copy.deepcopy(DRIVING)
ACCESS_DRIVING["route"]["paths"][0]["duration"] = "600"
ACCESS_DRIVING["route"]["paths"][0]["distance"] = "4200"
ACCESS_DRIVING["route"]["taxi_cost"] = "15.0"


def _access_args(**kw):
    return Args(**kw)


def test_resolve_access_no_access(costs: dict) -> None:
    acc_o, acc_d = tp.resolve_access(
        costs, _access_args(no_access=True), None, None, None, None, None,
        None, None, [])
    assert acc_o["minutes"] == 0 and acc_o["source"] == "人已在车站"
    assert acc_d["cost"] == 0.0


def test_resolve_access_manual_override(costs: dict) -> None:
    acc_o, _ = tp.resolve_access(
        costs, _access_args(access_origin_min=5, access_origin_cost=8), None,
        None, None, None, None, None, None, [])
    assert acc_o["minutes"] == 5 and acc_o["cost"] == 8.0
    assert acc_o["source"] == "手动指定"


def test_resolve_access_assume_mode(costs: dict) -> None:
    acc_o, acc_d = tp.resolve_access(
        costs, _access_args(access="assume"), None, (31.95, 119.17), (31.92, 119.17),
        "句容", "句容", "镇江", "镇江", [])
    assert acc_o["source"] == "假设值" and acc_o["minutes"] == costs["access"]["origin"]["minutes"]
    assert acc_d["minutes"] == costs["access"]["destination"]["minutes"]


def test_resolve_access_auto_picks_faster_taxi(costs: dict) -> None:
    amap = FakeAmap(transit=ACCESS_TRANSIT, driving=ACCESS_DRIVING)
    warnings: list[str] = []
    acc_o, _ = tp.resolve_access(
        costs, _access_args(), amap, (31.9584, 119.1995), None,
        "句容", None, "镇江", None, warnings, key_configured=True)
    assert acc_o["source"] == "高德打车"
    assert acc_o["minutes"] == 10  # 打车 10 分 < 公交 60 分
    assert "公交" in acc_o["alt"]


def test_resolve_access_transit_only(costs: dict) -> None:
    amap = FakeAmap(transit=ACCESS_TRANSIT, driving=ACCESS_DRIVING)
    acc_o, _ = tp.resolve_access(
        costs, _access_args(access_mode="transit"), amap, (31.9584, 119.1995), None,
        "句容", None, "镇江", None, [], key_configured=True)
    assert acc_o["source"] == "高德公交"
    assert acc_o["minutes"] == 60
    assert acc_o["walk_m"] == 300


def test_resolve_access_walks_when_station_is_close(costs: dict) -> None:
    amap = FakeAmap()
    acc_o, _ = tp.resolve_access(
        costs, _access_args(), amap, (31.9210, 119.1720), None,
        "句容", None, "镇江", None, [], key_configured=True)
    assert "步行" in acc_o["source"]
    assert acc_o["cost"] == 0.0


def test_resolve_access_falls_back_with_warning(costs: dict) -> None:
    amap = FakeAmap(fail={"direction/transit", "direction/driving"})
    warnings: list[str] = []
    acc_o, _ = tp.resolve_access(
        costs, _access_args(), amap, (31.9584, 119.1995), None,
        "句容", None, "镇江", None, warnings, key_configured=True)
    assert acc_o["source"] == "假设值"
    assert acc_o["minutes"] == costs["access"]["origin"]["minutes"]
    assert any("没取到公交方案" in w for w in warnings)


# --- 渲染 ------------------------------------------------------------------
def _result_for_render() -> dict:
    return {
        "data": {
            "from": "句容", "to": "昆山南", "date": "2026-10-24", "prefer": "balance",
            "assumptions": ["高德驾车路线", "坐标：A (31.95, 119.17)，B (31.97, 118.8)"],
            "warnings": ["示例告警"],
            "comparison": [{
                "mode": "transit", "name": "公共交通（含地铁）", "total_min": 115,
                "cost": 9.0, "confidence": "高德实测", "recommended": True,
                "detail": "3 段乘车 + 3 段步行，步行共 1993 米（约 32 分）",
                "note": "逐段见下",
                "legs": [
                    {"kind": "walk", "text": "步行 1291 米（约 20 分）到 2号口 进站"},
                    {"kind": "bus", "text": "地铁S6号线 崇明→马群（10 站，43 分）"},
                ],
            }],
        },
        "status": "success",
        "summary": "1 种方式可比",
        "next_action": "",
    }


def test_render_text_shows_every_leg() -> None:
    text = tp.render_text(_result_for_render())
    assert "步行 1291 米" in text
    assert "地铁S6号线" in text
    assert "示例告警" in text
    assert "推荐" in text


def test_evidence_summarises_modes() -> None:
    result = {
        "rail": {"available": True, "status": "success", "summary": "5 趟直达"},
        "transit": {"available": True, "duration_min": 115, "cost": 9.0,
                    "walk_m": 1993, "plans_found": 2},
        "taxi": {"available": True, "distance_km": 46.6, "duration_min": 49,
                 "cost": 126.0, "fare_source": "高德 taxi_cost"},
        "drive": {"available": True, "distance_km": 46.6, "cost": 28.0},
        "walk": {"available": True, "distance_km": 1.2, "duration_min": 16},
        "warnings": ["w1"],
    }
    ev = tp._evidence(result)
    joined = "\n".join(ev)
    assert "铁路：success" in joined
    assert "公共交通：115 分" in joined
    assert "打车：46.6 km" in joined
    assert "自驾" in joined and "步行：1.2 km" in joined
    assert "w1" in joined


def test_render_text_handles_empty_comparison() -> None:
    text = tp.render_text({
        "data": {"from": "A", "to": "B", "date": "2026-10-24", "prefer": "balance",
                 "assumptions": [], "warnings": [], "comparison": []},
        "status": "failure", "summary": "没有可用数据", "next_action": "给坐标",
    })
    assert "没有可比较的出行方式" in text
    assert "下一步：给坐标" in text


# --- 输入校验 --------------------------------------------------------------
def test_unknown_modes_are_dropped() -> None:
    args = _access_args(modes="plane", amap_key=None)  # 全都不认识 → 回退到全部方式
    args.from_name = None
    args.to_name = None
    args.from_place = None
    args.to_place = None
    args.from_coord = None
    args.to_coord = None
    args.date = "2026-10-24"
    args.prefer = "balance"
    args.transit_strategy = 0
    args.rail_script = None
    args.rail_min_transfer = 16
    args.rail_via = None
    args.rail_arg = []
    args.rail_timeout = 600
    args.max_rail_options = 2
    args.taxi_city = None
    args.from_city = None
    args.to_city = None
    result = tp.build_result(args, {"access": {"origin": {"minutes": 30, "cost": 30},
                                               "destination": {"minutes": 30, "cost": 30}},
                                    "taxi": {"default": {"base_fare": 13, "base_km": 3,
                                                         "per_km": 2.6},
                                             "cities": {}, "pickup_min": 5},
                                    "drive": {"fuel_cost_per_km": 0.6, "toll_per_km": 0.5,
                                              "highway_ratio": 0.8},
                                    "walk": {"max_km": 5, "speed_kmh": 4.8}})
    assert result["status"] == "failure"
    assert any("未知方式已忽略" in w for w in result["warnings"])
    assert any("需要坐标" in w for w in result["warnings"])
