"""离线假数据 + 假客户端，测试完全不联网。"""
from __future__ import annotations

import amap_client

DRIVING = {
    "status": "1", "info": "OK", "count": "1",
    "route": {
        "origin": "119.170000,31.950000",
        "destination": "118.797000,31.968000",
        "taxi_cost": "126.0",
        "paths": [{
            "distance": "46614", "duration": "2940", "strategy": "速度最快",
            "tolls": "0", "toll_distance": "0", "traffic_lights": "12",
            "cost": {"duration": "2940", "tolls": "0", "toll_distance": "0"},
        }],
    },
}

TRANSIT = {
    "status": "1", "info": "OK", "count": "2",
    "route": {
        "origin": "119.170000,31.950000",
        "destination": "118.797000,31.968000",
        "transits": [
            {
                "cost": "9.0", "duration": "6900", "walking_distance": [],
                "distance": "52000", "nightflag": "0",
                "segments": [
                    {"walking": {"distance": "1291", "duration": "1200",
                                 "steps": [{"instruction": "步行50米右转进入左侧道路",
                                            "distance": "50"}]},
                     "bus": {"buslines": []}, "railway": {"via_stops": [], "alters": [], "spaces": []},
                     "taxi": {}, "entrance": {"name": "2号口", "location": "119.178130,31.943320"}},
                    {"bus": {"buslines": [{
                        "name": "地铁S6号线(宁句线)(句容--马群)", "type": "地铁线路",
                        "distance": "40363", "duration": "2580",
                        "departure_stop": {"name": "崇明"},
                        "arrival_stop": {"name": "马群"}, "via_num": "10",
                    }]},
                     "walking": {}, "railway": {"via_stops": []}, "taxi": {}},
                    {"walking": {"distance": "379", "duration": "420",
                                 "steps": [{"instruction": "步行91米到达马群", "distance": "91"}]},
                     "railway": {"via_stops": [], "alters": [], "spaces": []}},
                    {"bus": {"buslines": [{
                        "name": "地铁2号线(经天路--鱼嘴)", "type": "地铁线路",
                        "distance": "7650", "duration": "840",
                        "departure_stop": {"name": "马群"},
                        "arrival_stop": {"name": "明故宫"}, "via_num": "4",
                    }]}},
                    {"walking": {"distance": "323", "duration": "300", "steps": []}},
                    {"bus": {"buslines": [{
                        "name": "地铁6号线(栖霞山--南京南站)", "type": "地铁线路",
                        "distance": "8965", "duration": "1080",
                        "departure_stop": {"name": "明故宫"},
                        "arrival_stop": {"name": "南京南站"}, "via_num": "6",
                    }]},
                     "exit": {"name": "8号口", "location": "118.796319,31.970442"}},
                ],
            },
            {
                "cost": "33.5", "duration": "17640", "walking_distance": "779",
                "distance": "210000",
                "segments": [
                    {"walking": {"distance": "755", "duration": "660",
                                 "steps": [{"instruction": "步行25米右转", "distance": "25"}]}},
                    {"bus": {"buslines": [{
                        "name": "611路(南风苑--镇江火车站(南))", "type": "普通公交",
                        "distance": "30000", "duration": "4440",
                        "departure_stop": {"name": "句容公安局"},
                        "arrival_stop": {"name": "镇江火车站(南)"}, "via_num": "10",
                    }]}},
                    {"railway": {
                        "id": "B001", "time": "10020", "name": "D126(杭州-西安)", "trip": "D126",
                        "type": "火车", "distance": "344329",
                        "departure_stop": {"name": "南京", "time": "2112"},
                        "arrival_stop": {"name": "徐州", "time": "2359"},
                        "via_stops": [], "alters": [], "spaces": [],
                    }},
                    {"taxi": {"origin": "", "destination": "", "distance": "5700",
                              "duration": "960", "driving_distance": "6200"}},
                ],
            },
        ],
    },
}

WALKING = {
    "status": "1", "info": "OK",
    "route": {"origin": "", "destination": "", "paths": [{
        "distance": "1200", "duration": "960",
        "steps": [{"instruction": "向东步行 500 米", "distance": "500"},
                  {"instruction": "左转步行 700 米", "distance": "700"}],
    }]},
}

PLACE = {
    "status": "1", "info": "OK", "count": "2",
    "pois": [
        {"name": "花香里营地", "address": "玉带北路369号", "adname": "昆山市",
         "cityname": "苏州市", "type": "体育休闲服务", "location": "120.898843,31.354666"},
        {"name": "花里香花店", "address": "某某路1号", "adname": "昆山市",
         "cityname": "苏州市", "type": "购物服务", "location": "120.99,31.39"},
    ],
}

REVERSE = {
    "status": "1", "info": "OK",
    "regeocode": {
        "formatted_address": "江苏省镇江市句容市华阳街道雅居乐花园",
        "addressComponent": {"city": "镇江市", "district": "句容市", "province": "江苏省"},
    },
}

GEOCODE = {
    "status": "1", "info": "OK", "count": "1",
    "geocodes": [{"formatted_address": "江苏省镇江市句容市句容站",
                  "location": "119.172022,31.920869"}],
}


class FakeAmap(amap_client.Amap):
    """把 _get 换掉，其余逻辑（归一化/解析）照常走。"""

    def __init__(self, payloads=None, *, driving=None, transit=None, walking=None,
                 place=None, reverse=None, geocode=None, fail=()):
        super().__init__("FAKE-KEY")
        self.payloads = {
            "direction/driving": driving if driving is not None else DRIVING,
            "direction/transit/integrated": transit if transit is not None else TRANSIT,
            "direction/walking": walking if walking is not None else WALKING,
            "place/text": place if place is not None else PLACE,
            "geocode/geo": geocode if geocode is not None else GEOCODE,
            "geocode/regeo": reverse if reverse is not None else REVERSE,
        }
        self.fail = set(fail)  # 指定哪些接口要抛 AmapError
        self.calls: list[tuple[str, dict]] = []

    def _get(self, url, params):
        self.calls.append((url, params))
        for key in self.fail:
            if key in url:
                raise amap_client.AmapError("模拟失败", "10003")
        for key, payload in self.payloads.items():
            if key in url:
                if payload is None:
                    raise amap_client.AmapError("模拟失败", "10003")
                return payload
        raise amap_client.AmapError("fixture missing", "99999")


class Args:
    """模拟 argparse 的结果，给 resolve_access 之类用。"""

    def __init__(self, **kw):
        self.no_access = False
        self.access = "auto"
        self.access_mode = "fastest"
        self.access_origin_min = None
        self.access_origin_cost = None
        self.access_dest_min = None
        self.access_dest_cost = None
        self.amap_key = None
        self.update(**kw)

    def update(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        return self
