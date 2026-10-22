#!/usr/bin/env python
"""高德 Web 服务 API 的最小封装（打车 / 步行 / 公共交通）。

只做 trip-planner 用到的四件事：
- 地理编码 / 逆地理编码（把站名、坐标换成可规划的点）
- 步行路径规划（v3/direction/walking）
- 公共交通路径规划（v3/direction/transit/integrated，含公交/地铁/跨城）
- 驾车路径规划（v3/direction/driving，extensions=all，含过路费与打车费估价）

返回的都是「已经归一化好的 dict」，调用方不用关心高德原始字段。
高德的数字基本都是字符串，这里统一转成数字，缺失就是 None。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

BASE = "https://restapi.amap.com/v3"
WALKING = f"{BASE}/direction/walking"
TRANSIT = f"{BASE}/direction/transit/integrated"
DRIVING = f"{BASE}/direction/driving"
GEOCODE = f"{BASE}/geocode/geo"
REGEO = f"{BASE}/geocode/regeo"
PLACE = f"{BASE}/place/text"


class AmapError(RuntimeError):
    """高德返回 status != 1，或网络层失败。"""

    def __init__(self, message: str, infocode: str | None = None):
        super().__init__(message)
        self.infocode = infocode


def _num(val) -> float | None:
    if val is None or val == "" or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val) -> int | None:
    n = _num(val)
    return None if n is None else int(n + 0.5)


def _coord_text(coord: tuple[float, float]) -> str:
    """(lat, lng) → 高德要的 "lng,lat"。"""
    return f"{coord[1]:.6f},{coord[0]:.6f}"


def _hhmm(val) -> str:
    """高德的 "2112" / "930" → "21:12" / "09:30"；拿不到就空串。"""
    text = str(val or "").strip()
    if not text.isdigit():
        return ""
    text = text.zfill(4)
    return f"{text[:2]}:{text[2:]}"


def _fmt_span(minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}小时{m}分"
    return f"{h}小时" if h else f"{m}分"


class Amap:
    def __init__(self, key: str, timeout: int = 20):
        if not key:
            raise AmapError("没有高德 Key")
        self.key = key
        self.timeout = timeout

    # --- 底层 --------------------------------------------------------------
    def _get(self, url: str, params: dict) -> dict:
        params = {**params, "key": self.key}
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{query}")  # noqa: S310  # 只用 https
        try:
            with urllib.request.urlopen(  # noqa: S310  # 同上，固定高德 https 域名
                    req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise AmapError(f"高德请求失败：{exc}") from exc
        if str(payload.get("status")) != "1":
            raise AmapError(str(payload.get("info") or "高德返回失败"),
                            str(payload.get("infocode") or ""))
        return payload

    def check(self) -> str:
        """用一次最便宜的逆地理编码确认 Key 有效，返回城市名。"""
        geo = self.regeo((39.90909, 116.434307))
        return (geo.get("city") or "未知城市")

    # --- 地理编码 ----------------------------------------------------------
    def geocode(self, name: str, city: str | None = None) -> tuple[float, float] | None:
        params: dict = {"address": name}
        if city:
            params["city"] = city
        try:
            payload = self._get(GEOCODE, params)
        except AmapError:
            return None
        geos = payload.get("geocodes") or []
        if not geos:
            return None
        loc = str(geos[0].get("location") or "")
        if "," not in loc:
            return None
        lng, lat = loc.split(",", 1)[:2]
        try:
            return (float(lat), float(lng))
        except ValueError:
            return None

    def regeo(self, coord: tuple[float, float]) -> dict:
        """逆地理编码：返回 {city, district, address}。"""
        payload = self._get(REGEO, {
            "location": _coord_text(coord),
            "extensions": "base",
        })
        comp = (payload.get("regeocode") or {}).get("addressComponent") or {}
        city = comp.get("city")
        if isinstance(city, list):  # 直辖市会返回空数组
            city = ""
        return {
            "city": (city or comp.get("province") or "").strip(),
            "district": (comp.get("district") or "").strip(),
            "address": (payload.get("regeocode") or {}).get("formatted_address") or "",
        }

    def city_of(self, coord: tuple[float, float]) -> str | None:
        try:
            city = self.regeo(coord).get("city") or ""
        except AmapError:
            return None
        return city.rstrip("市") or None

    # --- POI 关键字搜索（地址/店名 → 坐标）--------------------------------
    def place_text(self, keywords: str, city: str | None = None,
                   citylimit: bool = True, offset: int = 10) -> list[dict]:
        params: dict = {
            "keywords": keywords,
            "offset": min(max(offset, 1), 25),
            "page": 1,
            "extensions": "base",
        }
        if city:
            params["city"] = city
            params["citylimit"] = "true" if citylimit else "false"
        payload = self._get(PLACE, params)
        pois: list[dict] = []
        for p in payload.get("pois") or []:
            loc = str(p.get("location") or "")
            if "," not in loc:
                continue
            lng, lat = loc.split(",", 1)[:2]
            try:
                coord = (float(lat), float(lng))
            except ValueError:
                continue
            pois.append({
                "name": p.get("name") or "",
                "address": p.get("address") if isinstance(p.get("address"), str) else "",
                "district": p.get("adname") or "",
                "city": p.get("cityname") or "",
                "type": p.get("type") or "",
                "coord": coord,
            })
        return pois

    # --- 步行 --------------------------------------------------------------
    def walking(self, a: tuple[float, float], b: tuple[float, float]) -> dict | None:
        try:
            payload = self._get(WALKING, {
                "origin": _coord_text(a),
                "destination": _coord_text(b),
            })
        except AmapError:
            return None
        paths = (payload.get("route") or {}).get("paths") or []
        if not paths:
            return None
        p = paths[0]
        steps = [
            {
                "instruction": (s.get("instruction") or "").strip(),
                "distance_m": _int(s.get("distance")) or 0,
            }
            for s in (p.get("steps") or [])
        ]
        dist_m = _int(p.get("distance"))
        dur = _int(p.get("duration"))
        return {
            "distance_km": None if dist_m is None else round(dist_m / 1000.0, 1),
            "distance_m": dist_m,
            "duration_min": None if dur is None else max(1, round(dur / 60)),
            "steps": steps,
        }

    # --- 公共交通 ----------------------------------------------------------
    def transit(self, a: tuple[float, float], b: tuple[float, float],
                city: str, cityd: str | None = None,
                strategy: int = 0, nightflag: int = 0) -> list[dict]:
        params: dict = {
            "origin": _coord_text(a),
            "destination": _coord_text(b),
            "city": city,
            "strategy": strategy,
            "nightflag": nightflag,
        }
        if cityd and cityd != city:
            params["cityd"] = cityd
        payload = self._get(TRANSIT, params)
        plans: list[dict] = []
        for t in (payload.get("route") or {}).get("transits") or []:
            plans.append(self._normalize_transit(t))
        return plans

    @staticmethod
    def _normalize_transit(t: dict) -> dict:
        legs: list[dict] = []
        walk_m = 0
        walk_min = 0
        for seg in t.get("segments") or []:
            walking = seg.get("walking")
            if isinstance(walking, dict):
                dist_m = _int(walking.get("distance")) or 0
                dur = _int(walking.get("duration")) or 0
                steps = [
                    {
                        "instruction": (s.get("instruction") or "").strip(),
                        "distance_m": _int(s.get("distance")) or 0,
                    }
                    for s in (walking.get("steps") or [])
                ]
                if dist_m or dur or steps:
                    walk_min_leg = max(1, round(dur / 60)) if dur else 0
                    walk_m += dist_m
                    walk_min += walk_min_leg
                    entrance = (seg.get("entrance") or {}).get("name") or ""
                    hint = next((s["instruction"] for s in steps if s["instruction"]), "")
                    if len(hint) > 30:
                        hint = hint[:30] + "…"
                    text = f"步行 {dist_m} 米（约 {walk_min_leg or 1} 分）"
                    if entrance:
                        text += f" 到 {entrance} 进站"
                    elif hint:
                        text += f"：{hint}"
                    legs.append({
                        "kind": "walk",
                        "distance_m": dist_m,
                        "duration_min": walk_min_leg,
                        "steps": steps,
                        "entrance": entrance or None,
                        "text": text,
                    })
            bus = seg.get("bus")
            if isinstance(bus, dict):
                for line in bus.get("buslines") or []:
                    dep = (line.get("departure_stop") or {}).get("name") or ""
                    arr = (line.get("arrival_stop") or {}).get("name") or ""
                    via = _int(line.get("via_num")) or 0
                    dur = _int(line.get("duration")) or 0
                    exit_name = (seg.get("exit") or {}).get("name") or ""
                    legs.append({
                        "kind": "bus",
                        "line": line.get("name") or "",
                        "line_type": line.get("type") or "",
                        "from": dep,
                        "to": arr,
                        "stops": via,
                        "duration_min": round(dur / 60) if dur else None,
                        "exit": exit_name or None,
                        "text": (f"{line.get('name') or '公交'}  {dep}→{arr}"
                                 f"（{via} 站{'' if not dur else f'，{round(dur / 60)} 分'}）"
                                 + (f"，{exit_name} 出站" if exit_name else "")),
                    })
            railway = seg.get("railway")
            if isinstance(railway, dict) and (railway.get("trip") or railway.get("name")
                                              or railway.get("via_stops")):
                dep_stop = railway.get("departure_stop") or {}
                arr_stop = railway.get("arrival_stop") or {}
                dep = dep_stop.get("name") or ""
                arr = arr_stop.get("name") or ""
                name = railway.get("trip") or railway.get("name") or "火车"
                dur_min = round((_int(railway.get("time")) or 0) / 60) or None
                dep_t = _hhmm(dep_stop.get("time"))
                arr_t = _hhmm(arr_stop.get("time"))
                legs.append({
                    "kind": "rail",
                    "line": name,
                    "from": dep,
                    "to": arr,
                    "dep": dep_t or None,
                    "arr": arr_t or None,
                    "duration_min": dur_min,
                    "text": (f"{name}  {dep}"
                             + (f" {dep_t}" if dep_t else "")
                             + f"→{arr}"
                             + (f" {arr_t}" if arr_t else "")
                             + (f"（{_fmt_span(dur_min)}）" if dur_min else "")),
                })
            taxi = seg.get("taxi")
            if isinstance(taxi, dict) and (_int(taxi.get("distance")) or _int(taxi.get("duration"))):
                dist_m = _int(taxi.get("distance")) or 0
                dur = _int(taxi.get("duration")) or 0
                legs.append({
                    "kind": "taxi",
                    "distance_m": dist_m,
                    "duration_min": round(dur / 60) if dur else None,
                    "text": f"打车 {round(dist_m / 1000.0, 1)} km"
                            f"{'' if not dur else f'（约 {round(dur / 60)} 分）'}",
                })

        cost = _num(t.get("cost"))
        dur_s = _int(t.get("duration")) or 0
        kinds = {leg["kind"] for leg in legs}
        dist_m = _int(t.get("distance"))
        has_subway = any(
            leg["kind"] == "bus"
            and ("地铁" in (leg.get("line_type") or "") or "地铁" in (leg.get("line") or ""))
            for leg in legs)
        return {
            "duration_min": max(1, round(dur_s / 60)) if dur_s else None,
            "cost": cost,
            "walk_m": walk_m,
            "walk_min": walk_min,
            "distance_km": None if dist_m is None else round(dist_m / 1000.0, 1),
            "legs": legs,
            "has_subway": has_subway,
            "has_rail": "rail" in kinds,
            "text": " → ".join(leg["text"] for leg in legs),
        }

    # --- 驾车 --------------------------------------------------------------
    def driving(self, a: tuple[float, float], b: tuple[float, float],
                strategy: int = 0) -> dict | None:
        del strategy  # 目前只用默认策略，保留参数方便以后扩展
        try:
            payload = self._get(DRIVING, {
                "origin": _coord_text(a),
                "destination": _coord_text(b),
                "extensions": "all",
            })
        except AmapError:
            return None
        route = payload.get("route") or {}
        paths = route.get("paths") or []
        if not paths:
            return None
        p = paths[0]
        dist_m = _int(p.get("distance"))
        dur_s = _int(p.get("duration"))
        cost = p.get("cost") if isinstance(p.get("cost"), dict) else {}
        tolls = _num(cost.get("tolls")) or _num(p.get("tolls"))
        return {
            "distance_km": None if dist_m is None else round(dist_m / 1000.0, 1),
            "duration_min": None if dur_s is None else max(1, round(dur_s / 60)),
            "tolls": tolls,
            "taxi_cost": _num(route.get("taxi_cost")),
            "traffic_lights": _int(p.get("traffic_lights")),
            "strategy": p.get("strategy"),
        }
