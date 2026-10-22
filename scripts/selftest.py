#!/usr/bin/env python
"""离线自测：不联网，用假的 json 验证高德返回值的解析。

直接跑：python scripts/selftest.py
有真实 Key 时更该跑一遍真实查询（--detect + 一次 A→B）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import amap_client

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ✅ {name}")
    else:
        FAILED.append(name)
        print(f"  ❌ {name} {detail}")


DRIVING_FIXTURE = {
    "status": "1", "info": "OK", "count": "1",
    "route": {
        "origin": "119.170000,31.950000",
        "destination": "118.797000,31.968000",
        "taxi_cost": "128.5",
        "paths": [{
            "distance": "49321", "duration": "3240", "strategy": "速度最快",
            "tolls": "15", "toll_distance": "30000", "traffic_lights": "12",
            "cost": {"duration": "3240", "tolls": "15", "toll_distance": "30000"},
        }],
    },
}

TRANSIT_FIXTURE = {
    "status": "1", "info": "OK", "count": "2",
    "route": {
        "origin": "119.170000,31.950000",
        "destination": "118.797000,31.968000",
        "transits": [
            {
                "cost": "6.0", "duration": "3720", "walking_distance": "820",
                "distance": "41200", "nightflag": "0",
                "segments": [
                    {"walking": {"origin": "", "destination": "",
                                 "distance": "350", "duration": "300",
                                 "steps": [{"instruction": "向东步行 350 米", "distance": "350"}]}},
                    {"bus": {"buslines": [{
                        "name": "地铁S1号线(翔宇路南--空港新城江宁)",
                        "type": "地铁", "distance": "32000", "duration": "2400",
                        "departure_stop": {"name": "句容站"},
                        "arrival_stop": {"name": "南京南站"},
                        "via_num": "9",
                    }]}},
                    {"walking": {"origin": "", "destination": "",
                                 "distance": "470", "duration": "420", "steps": []}},
                ],
            },
            {
                "cost": "3.0", "duration": "5400", "walking_distance": "1500",
                "distance": "39000",
                "segments": [
                    {"walking": {"distance": "1500", "duration": "1300", "steps": []}},
                    {"bus": {"buslines": [{
                        "name": "句容-南京 长途大巴", "type": "普通公交",
                        "distance": "37500", "duration": "4100",
                        "departure_stop": {"name": "句容汽车站"},
                        "arrival_stop": {"name": "南京汽车站"},
                        "via_num": "3",
                    }]}},
                ],
            },
        ],
    },
}

WALKING_FIXTURE = {
    "status": "1", "info": "OK",
    "route": {"origin": "", "destination": "", "paths": [{
        "distance": "1200", "duration": "960",
        "steps": [
            {"instruction": "向东步行 500 米", "distance": "500"},
            {"instruction": "左转步行 700 米", "distance": "700"},
        ],
    }]},
}


class FakeAmap(amap_client.Amap):
    """把 _get 换掉，其余逻辑照走。"""

    def __init__(self, fixtures):
        super().__init__("FAKE-KEY")
        self.fixtures = fixtures

    def _get(self, url, params):
        for key, payload in self.fixtures.items():
            if key in url:
                return payload
        raise amap_client.AmapError("fixture missing", "99999")


def test_driving() -> None:
    print("驾车路线（含 taxi_cost / 过路费）")
    amap = FakeAmap({"direction/driving": DRIVING_FIXTURE})
    r = amap.driving((31.95, 119.17), (31.968, 118.797))
    check("distance_km ≈ 49.3", r and abs(r["distance_km"] - 49.3) < 0.05, str(r))
    check("duration_min = 54", r and r["duration_min"] == 54, str(r))
    check("tolls = 15", r and r["tolls"] == 15, str(r))
    check("taxi_cost = 128.5", r and r["taxi_cost"] == 128.5, str(r))


def test_transit() -> None:
    print("公共交通（地铁段 / 公交段 / 步行段）")
    amap = FakeAmap({"transit/integrated": TRANSIT_FIXTURE})
    plans = amap.transit((31.95, 119.17), (31.968, 118.797), "句容", "南京")
    check("返回 2 条方案", len(plans) == 2, str(len(plans)))
    p = plans[0]
    check("耗时 62 分", p["duration_min"] == 62, str(p["duration_min"]))
    check("票价 6 元", p["cost"] == 6.0, str(p["cost"]))
    check("步行合计 820 米", p["walk_m"] == 820, str(p["walk_m"]))
    check("识别出地铁", p["has_subway"] is True)
    check("步行段 2 段", sum(1 for leg in p["legs"] if leg["kind"] == "walk") == 2)
    check("地铁段站名正确",
          any(leg.get("from") == "句容站" and leg.get("to") == "南京南站" for leg in p["legs"]))
    check("文字里有步行", "步行" in p["text"], p["text"])
    p2 = plans[1]
    check("第二条不是地铁", p2["has_subway"] is False)
    check("第二条是公交", any(leg["kind"] == "bus" for leg in p2["legs"]))


def test_walking() -> None:
    print("步行规划")
    amap = FakeAmap({"direction/walking": WALKING_FIXTURE})
    r = amap.walking((31.95, 119.17), (31.952, 119.172))
    check("1.2 km", r and r["distance_km"] == 1.2, str(r))
    check("16 分", r and r["duration_min"] == 16, str(r))
    check("2 段步行指引", r and len(r["steps"]) == 2, str(r))


def test_errors() -> None:
    print("错误处理")
    amap = FakeAmap({})  # 任何 URL 都抛 AmapError
    check("walking 失败返回 None", amap.walking((31.95, 119.17), (31.96, 119.18)) is None)
    check("transit 失败向上抛", _raises(lambda: amap.transit((31.95, 119.17), (31.96, 119.18), "句容")))


def _raises(fn) -> bool:
    try:
        fn()
    except amap_client.AmapError:
        return True
    return False


def main() -> int:
    print("=== amap_client 离线自测 ===")
    test_driving()
    test_transit()
    test_walking()
    test_errors()
    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
