#!/usr/bin/env python
"""行程方式对比：铁路（复用 rail-transfer skill）+ 公共交通 / 打车 / 自驾 / 步行。

设计原则：
- 铁路数据一律由 rail-transfer skill 查询（不重复实现 12306 对接）；
- 打车、公共交通、步行一律走高德 Web 服务 API（真实线路、真实里程时长），
  没有高德 Key 时降级为「本地计价规则 + 直线距离」估算，并明确标注；
- 门到门接驳默认也走高德公交规划（含步行段），拿不到才退回假设值；
- 所有假设都显式输出，并标注可信度（实测 / 高德 / 估算 / 假设）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = SKILL_DIR.parent
DEFAULT_RAIL_SCRIPT = SKILLS_DIR / "rail-transfer" / "scripts" / "rail_transfer.py"

if str(SKILL_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
import amap_client  # noqa: E402  # 同目录的兄弟模块，靠上面的 sys.path 兜底

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001, S110  # 老终端不支持 reconfigure，忽略即可
    pass

FC_ENV = "environment-missing"
FC_CONN = "connection-failure"
FC_AMBIG = "ambiguous-context"
FC_INPUT = "invalid-input"

MODES = ("rail", "transit", "taxi", "drive", "walk")
VERSION = "0.2.0"


# --- 基础工具 --------------------------------------------------------------
def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _price_num(text: Any) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    if not isinstance(text, str):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def _duration_min(text: Any) -> int | None:
    if not isinstance(text, str) or ":" not in text:
        return None
    m = re.match(r"^(\d{1,3}):(\d{2})$", text.strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _fmt_money(val: float | None) -> str:
    return "—" if val is None else f"¥{val:g}"


def _fmt_min(total: int | None) -> str:
    if total is None:
        return "—"
    h, m = divmod(int(total), 60)
    if h and m:
        return f"{h}小时{m}分"
    if h:
        return f"{h}小时"
    return f"{m}分"


def _parse_coord(text: str) -> tuple[float, float] | None:
    """接受 "lat,lng"（纬度在前），也容忍全角逗号/空格。"""
    if not text:
        return None
    parts = re.split(r"[,，\s]+", text.strip())
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return (lat, lng)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# --- 高德（可选）-----------------------------------------------------------
def make_amap(key: str, timeout: int = 20) -> amap_client.Amap | None:
    """有 Key 就返回客户端；没有就返回 None（调用方走降级估算）。"""
    if not key:
        return None
    return amap_client.Amap(key, timeout=timeout)


def amap_unavailable_reason(exc: Exception) -> str:
    if isinstance(exc, amap_client.AmapError):
        code = exc.infocode or ""
        if code == "10001":
            return "高德 Key 无效（INVALID_USER_KEY），请在控制台确认 Key 与「Web服务」平台"
        if code == "10003":
            return "高德 Key 今日配额已用完（DAILY_QUERY_OVER_LIMIT）"
        if code == "10009":
            return "高德 Key 平台类型不对（应用类型不是「Web服务」）"
        if code == "10021":
            return "高德请求太频繁（QPS 超限），稍等一下重试"
        return f"高德接口报错：{exc}"
    return f"高德请求失败：{exc}"


def resolve_place(amap, text: str | None, city: str | None,
                  warnings: list[str], label: str) -> tuple[float, float] | None:
    """把「句容雅居乐花园」这类地址/店名用 POI 搜索换成坐标。"""
    text = (text or "").strip()
    if not amap or not text:
        return None
    try:
        pois = amap.place_text(text, city)
    except amap_client.AmapError as exc:
        warnings.append(f"{label} POI 搜索失败：{amap_unavailable_reason(exc)}")
        return None
    if not pois:
        warnings.append(f"{label}按「{text}」没搜到 POI，请直接给坐标")
        return None
    # 优先选名字对得上的（含查询词），否则用第一条
    pick = next((p for p in pois if text in p["name"] or p["name"] in text), pois[0])
    where = " ".join(x for x in (pick["city"], pick["district"], pick["address"]) if x)
    warnings.append(f"{label}坐标按高德 POI「{pick['name']}」（{where}）解析，"
                    f"若不对请用 --from-coord/--to-coord 指定")
    return pick["coord"]


# --- 计价模型 --------------------------------------------------------------
def pick_city_rule(costs: dict, city: str | None) -> tuple[str, dict]:
    taxi = costs["taxi"]
    cities = taxi.get("cities") or {}
    if city and city in cities:
        return city, {**taxi["default"], **cities[city]}
    for name in cities:
        if city and name in city:
            return name, {**taxi["default"], **cities[name]}
    return "默认", dict(taxi["default"])


def taxi_fare(rule: dict, km: float) -> float:
    base = float(rule.get("base_fare", 13))
    base_km = float(rule.get("base_km", 3))
    per_km = float(rule.get("per_km", 2.6))
    fare = base + max(0.0, km - base_km) * per_km
    return max(fare, float(rule.get("min_fare", base)))


def drive_cost(costs: dict, km: float, tolls: float | None = None) -> float:
    """油/电 + 过路费；有高德实测过路费时优先用它。"""
    d = costs["drive"]
    fuel = float(d.get("fuel_cost_per_km", 0.6)) * km
    if tolls is None:
        tolls = float(d.get("toll_per_km", 0.5)) * km * float(d.get("highway_ratio", 0.8))
    return fuel + tolls


def walk_minutes(costs: dict, km: float) -> int:
    speed = float(costs.get("walk", {}).get("speed_kmh", 4.8))
    return max(1, int(km / speed * 60 + 0.5))


def pick_transit_plan(plans: list[dict], prefer: str) -> dict | None:
    """公共交通方案里按偏好挑一条。"""
    if not plans:
        return None

    def tm(plan: dict) -> float:
        return plan.get("duration_min") or 1e9

    def cm(plan: dict) -> float:
        cost = plan.get("cost")
        return 1e9 if cost is None else cost

    if prefer == "price":
        return min(plans, key=lambda p: (cm(p), tm(p)))
    if prefer == "time":
        return min(plans, key=lambda p: (tm(p), cm(p)))
    by_t = sorted(plans, key=tm)
    by_c = sorted(plans, key=cm)
    tr = {id(p): i for i, p in enumerate(by_t)}
    cr = {id(p): i for i, p in enumerate(by_c)}
    return min(plans, key=lambda p: (tr[id(p)] + cr[id(p)], tr[id(p)], cr[id(p)]))


def estimate_distance(costs: dict, straight_km: float) -> tuple[float, int]:
    taxi = costs["taxi"]
    km = straight_km * float(taxi.get("detour_factor", 1.4))
    city_lim = float(taxi.get("intercity_threshold_km", 30))
    long_lim = float(taxi.get("long_trip_threshold_km", 120))
    if km <= city_lim:
        speed = float(taxi.get("city_speed_kmh", 26))
    elif km <= long_lim:
        speed = float(taxi.get("intercity_speed_kmh", 55))
    else:
        speed = float(taxi.get("long_speed_kmh", 80))
    return km, max(1, int(km / speed * 60 + 0.5))


# --- 铁路：调用 rail-transfer ----------------------------------------------
def find_rail_script(explicit: str | None) -> Path | None:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("RAIL_TRANSFER_SCRIPT")
    if env:
        candidates.append(Path(env))
    candidates.append(DEFAULT_RAIL_SCRIPT)
    for p in candidates:
        if p.is_file():
            return p
    return None


def call_rail(script: Path, rail_from: str, rail_to: str, date_str: str,
              min_transfer: int | None, via: str | None,
              extra_args: list[str], timeout: int) -> dict:
    result = _call_rail_once(script, rail_from, rail_to, date_str, min_transfer,
                             via, extra_args, timeout)
    # 余票缓存里可能留着「没车」的旧结果，空结果时强制重查一次
    if result.get("ok") and not result["data"].get("direct") \
            and not result["data"].get("plans") \
            and "--no-cache" not in extra_args:
        retry = _call_rail_once(script, rail_from, rail_to, date_str, min_transfer,
                                via, [*extra_args, "--no-cache"], timeout)
        if retry.get("ok") and (retry["data"].get("direct") or retry["data"].get("plans")):
            retry["summary"] = f"{retry.get('summary')}（已忽略旧缓存重查）"
            return retry
    return result


def _call_rail_once(script: Path, rail_from: str, rail_to: str, date_str: str,
                    min_transfer: int | None, via: str | None,
                    extra_args: list[str], timeout: int) -> dict:
    cmd = [sys.executable, str(script),
           "--from", rail_from, "--to", rail_to,
           "--date", date_str, "--format", "json"]
    if min_transfer is not None:
        cmd += ["--min-transfer", str(min_transfer)]
    if via:
        cmd += ["--via", via]
    cmd += list(extra_args)
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True,  # noqa: S603
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "failure_category": FC_CONN,
                "summary": f"rail-transfer 查询超时（>{timeout}s）"}
    out = (proc.stdout or "").strip()
    start = out.find("{")
    if start < 0:
        return {"ok": False, "failure_category": FC_CONN,
                "summary": "rail-transfer 没有返回 JSON",
                "stderr": (proc.stderr or "")[-800:]}
    try:
        payload = json.loads(out[start:])
    except json.JSONDecodeError:
        return {"ok": False, "failure_category": FC_CONN,
                "summary": "rail-transfer 返回的 JSON 无法解析"}
    data = payload.get("data") or {}
    return {"ok": True, "payload": payload, "data": data,
            "status": payload.get("status"),
            "summary": payload.get("summary"),
            "evidence": payload.get("evidence") or [],
            "failure_category": payload.get("failure_category"),
            "next_action": payload.get("next_action")}


def normalize_rail(rail: dict) -> list[dict]:
    """把 rail-transfer 的直达/换乘结果统一成可比较的条目。"""
    if not rail.get("ok"):
        return []
    data = rail["data"]
    options: list[dict] = []
    for t in data.get("direct") or []:
        dur = _duration_min(t.get("lishi"))
        options.append({
            "kind": "direct",
            "total_min": dur,
            "cost": _price_num(t.get("selected_price")),
            "dep": t.get("dep"), "arr": t.get("arr"),
            "label": f"{t.get('code')} {t.get('from_name')} {t.get('dep')} → "
                     f"{t.get('to_name')} {t.get('arr')}",
            "legs": [t.get("code")],
            "from_station": t.get("from_name"),
            "to_station": t.get("to_name"),
            "transfers": 0,
            "rail_min": dur,
            "wait_min": 0,
        })
    for p in data.get("plans") or []:
        legs = p.get("legs") or []
        label = " + ".join(
            f"{leg.get('code')} {leg.get('from_name')} {leg.get('dep')}→"
            f"{leg.get('to_name')} {leg.get('arr')}" for leg in legs)
        options.append({
            "kind": "transfer",
            "total_min": p.get("total_min"),
            "cost": _price_num(p.get("total_price")),
            "dep": legs[0].get("dep") if legs else "",
            "arr": legs[-1].get("arr") if legs else "",
            "label": label,
            "legs": [leg.get("code") for leg in legs],
            "from_station": legs[0].get("from_name") if legs else None,
            "to_station": legs[-1].get("to_name") if legs else None,
            "transfers": len(p.get("transfers") or []),
            "rail_min": p.get("rail_min"),
            "wait_min": p.get("wait_min"),
        })
    return options


def _dedupe_by_dep_arr(options: list[dict]) -> list[dict]:
    """同一组出发/到达时刻（换中转站凑出来的等价方案）只留最便宜的一条。"""
    best: dict[tuple, dict] = {}
    for o in options:
        key = (o.get("dep"), o.get("arr"))
        cur = best.get(key)
        if cur is None or (o.get("cost") or 1e18) < (cur.get("cost") or 1e18):
            best[key] = o
    return list(best.values())


def rank_entries(entries: list[dict], prefer: str) -> list[dict]:
    """按偏好排序，并给每条打上 time_rank / cost_rank / score。

    - time   ：只看总耗时（同耗时比价格）
    - price  ：只看总费用（同价格比耗时）
    - balance：时间排名 + 费用排名，平手优先更快
    """
    by_time = sorted(entries, key=lambda e: (e.get("total_min") is None,
                                             e.get("total_min") or 1e9))
    by_cost = sorted(entries, key=lambda e: (e.get("cost") is None,
                                             e.get("cost") or 1e18))
    time_rank = {id(e): i for i, e in enumerate(by_time)}
    cost_rank = {id(e): i for i, e in enumerate(by_cost)}
    for e in entries:
        t, c = time_rank[id(e)], cost_rank[id(e)]
        e["time_rank"] = t
        e["cost_rank"] = c
        if prefer == "time":
            e["score"] = (t, c)
        elif prefer == "price":
            e["score"] = (c, t)
        else:
            e["score"] = (t + c, t, c)
    return sorted(entries, key=lambda e: e["score"])


def select_rail_options(options: list[dict], prefer: str, limit: int = 2) -> list[dict]:
    """从多趟铁路方案里挑出最值得比较的几趟：有票价的优先，再按偏好排序。"""
    priced = [o for o in options if o.get("cost") is not None]
    pool = _dedupe_by_dep_arr(priced or options)
    return rank_entries([dict(o) for o in pool], prefer)[:max(1, limit)]


def build_trends(entries: list[dict], prefer: str) -> list[dict]:
    """给每条加 time_rank / cost_rank / 推荐标记（原地改，返回排好序的列表）。"""
    ordered = rank_entries(entries, prefer)
    best = ordered[0] if ordered else None
    for e in ordered:
        e["recommended"] = e is best
    return ordered


# --- 对比 ------------------------------------------------------------------


def _station_coord(amap, name: str | None, city: str | None,
                   cache: dict) -> tuple[float, float] | None:
    """把「句容」「南京南」这类站名换成坐标；高德地理编码偶尔要带城市才准。"""
    if not amap or not name:
        return None
    cache_key = (name, city or "")
    if cache_key in cache:
        return cache[cache_key]
    queries = [name if name.endswith("站") else f"{name}站", name]
    coord = None
    for q in queries:
        try:
            coord = amap.geocode(q, city)
        except amap_client.AmapError:
            coord = None
        if coord:
            break
    cache[cache_key] = coord
    return coord


def resolve_access(cfg: dict, args, amap, from_coord, to_coord,
                   station_from: str | None, station_to: str | None,
                   city_a: str | None, city_b: str | None,
                   warnings: list[str], key_configured: bool = False) -> tuple[dict, dict]:
    """算两端接驳：真人就在车站 → 手动指定 → 高德公交/步行规划 → 假设值。"""
    cache: dict = {}
    base_o = cfg["access"]["origin"]
    base_d = cfg["access"]["destination"]

    def one_side(side: str, start, end, city, station, explicit_min, explicit_cost):
        base = base_o if side == "origin" else base_d
        label = "A→出发站" if side == "origin" else "到达站→B"
        if getattr(args, "no_access", False):
            return {"minutes": 0, "cost": 0.0, "source": "人已在车站"}
        if explicit_min is not None or explicit_cost is not None:
            return {"minutes": explicit_min if explicit_min is not None else base["minutes"],
                    "cost": float(explicit_cost) if explicit_cost is not None
                            else float(base["cost"]),
                    "source": "手动指定"}
        if (getattr(args, "access", "auto") or "auto") == "auto" and amap and start and end:
            straight_km = haversine_km(start, end)
            if straight_km < 0.4:  # 人就在站门口，别硬套 30 分钟
                walk = amap.walking(start, end)
                if walk and walk.get("duration_min"):
                    return {"minutes": walk["duration_min"], "cost": 0.0,
                            "source": "高德步行（就在站旁）",
                            "detail": f"步行 {walk.get('distance_km')} km", "walk_m":
                            int((walk.get("distance_km") or 0) * 1000), "station": station or ""}
                return {"minutes": max(1, walk_minutes(cfg, straight_km)), "cost": 0.0,
                        "source": "步行估算（就在站旁）", "station": station or ""}
            mode = getattr(args, "access_mode", "fastest") or "fastest"
            transit_opt = None
            if mode in ("fastest", "transit"):
                try:
                    plans = amap.transit(start, end, city or "")
                    plan = pick_transit_plan(plans, "time")
                except amap_client.AmapError as exc:
                    plan = None
                    warnings.append(f"{label}公交规划失败：{amap_unavailable_reason(exc)}")
                if plan and plan.get("duration_min"):
                    cost = plan.get("cost")
                    transit_opt = {
                        "minutes": plan["duration_min"],
                        "cost": float(cost) if cost is not None else 0.0,
                        "source": "高德公交",
                        "detail": " → ".join(leg["text"].split("：")[0]
                                            for leg in plan.get("legs") or []),
                        "walk_m": plan.get("walk_m") or 0,
                        "legs": plan.get("legs") or [],
                        "station": station or "",
                    }
            taxi_opt = None
            if mode in ("fastest", "taxi"):
                drv = amap.driving(start, end)
                if drv and drv.get("duration_min"):
                    _city_name, rule = pick_city_rule(cfg, city or "")
                    fare = drv.get("taxi_cost")
                    fare = float(fare) if fare is not None else taxi_fare(rule, drv["distance_km"])
                    taxi_opt = {
                        "minutes": drv["duration_min"],
                        "cost": round(fare, 1),
                        "source": "高德打车",
                        "detail": f"打车 {drv['distance_km']} km / {drv['duration_min']} 分 / "
                                  f"¥{fare:g}",
                        "walk_m": 0,
                        "station": station or "",
                    }
            if mode == "transit" and transit_opt:
                return transit_opt
            if mode == "taxi" and taxi_opt:
                return taxi_opt
            if transit_opt and taxi_opt:  # fastest：取时间短的，时间相同取便宜的
                best, other = ((taxi_opt, transit_opt)
                               if (taxi_opt["minutes"], taxi_opt["cost"]) <=
                                  (transit_opt["minutes"], transit_opt["cost"])
                               else (transit_opt, taxi_opt))
                best = dict(best)
                best["alt"] = (f"{other['source']} {other['minutes']} 分 / ¥{other['cost']:g}")
                return best
            if transit_opt or taxi_opt:
                return transit_opt or taxi_opt
            # 公交/打车都没方案：短距离退到步行，长距离退到假设值并告警
            if amap and straight_km <= 3:
                walk = amap.walking(start, end)
                if walk and walk.get("duration_min"):
                    return {"minutes": walk["duration_min"], "cost": 0.0,
                            "source": "高德步行（无公交方案）",
                            "detail": f"步行 {walk.get('distance_km')} km",
                            "walk_m": int((walk.get("distance_km") or 0) * 1000),
                            "station": station or ""}
            warnings.append(f"{label}没取到公交方案，按假设值 {base['minutes']} 分 / "
                            f"¥{base['cost']:g} 计算")
        elif (getattr(args, "access", "auto") or "auto") == "auto" and key_configured:
            warnings.append(f"{label}没拿到站点坐标，按假设值计算")
        return {"minutes": base["minutes"], "cost": float(base["cost"]),
                "source": "假设值", "station": station or ""}

    acc_o = one_side("origin", from_coord,
                     _station_coord(amap, station_from, city_a, cache),
                     city_a, station_from,
                     args.access_origin_min, args.access_origin_cost)
    acc_d = one_side("destination",
                     _station_coord(amap, station_to, city_b, cache), to_coord,
                     city_b, station_to,
                     args.access_dest_min, args.access_dest_cost)
    return acc_o, acc_d


def render_text(result: dict) -> str:
    lines: list[str] = []
    d = result["data"]
    lines.append(f"🧭 {d['from']} → {d['to']}    {d['date']}    偏好：{d['prefer']}")
    lines.append("")
    comp = d.get("comparison") or []
    if not comp:
        lines.append("（没有可比较的出行方式）")
    else:
        lines.append("━━ 方式对比 ━━")
        for i, e in enumerate(comp, 1):
            mark = "  ← 推荐" if e.get("recommended") else ""
            lines.append(f"{i:>2}. {e['name']}   总耗时 {_fmt_min(e['total_min'])}   "
                         f"总费用 {_fmt_money(e['cost'])}   [{e['confidence']}]{mark}")
            if e.get("detail"):
                lines.append(f"    {e['detail']}")
            for j, leg in enumerate(e.get("legs") or [], 1):
                tag = {"walk": "🚶", "bus": "🚇", "rail": "🚆", "taxi": "🚕"}.get(
                    leg.get("kind"), "·")
                lines.append(f"      {j}. {tag} {leg.get('text') or ''}")
            if e.get("note"):
                lines.append(f"    说明：{e['note']}")
        lines.append("")
    conf = (d.get("rail") or {}).get("needs_confirmation") or []
    if conf:
        lines.append("━━ 需要确认 ━━")
        for c in conf:
            lines.append(f"   {c}")
        lines.append("")
    lines.append("━━ 假设与数据来源 ━━")
    for a in d.get("assumptions") or []:
        lines.append(f"   · {a}")
    for w in d.get("warnings") or []:
        lines.append(f"   ⚠ {w}")
    lines.append("")
    lines.append(f"状态：{result['status']} · {result['summary']}")
    if result.get("next_action"):
        lines.append(f"下一步：{result['next_action']}")
    return "\n".join(lines)


def resolve_amap_key(args) -> tuple[str, str]:
    """按优先级找高德 Key：命令行 > 环境变量 > references/amap.json。"""
    key = (getattr(args, "amap_key", None) or "").strip()
    if key:
        return key, "命令行 --amap-key"
    key = (os.environ.get("AMAP_KEY") or "").strip()
    if key:
        return key, "环境变量 AMAP_KEY"
    cfg_path = SKILL_DIR / "references" / "amap.json"
    if cfg_path.is_file():
        try:
            key = str(_load_json(cfg_path).get("key") or "").strip()
        except (OSError, ValueError):
            key = ""
        if key:
            return key, "references/amap.json"
    return "", ""


def build_result(args, cfg: dict) -> dict:
    warnings: list[str] = []
    assumptions: list[str] = []
    rail_script = find_rail_script(args.rail_script)
    from_coord = _parse_coord(args.from_coord or "")
    to_coord = _parse_coord(args.to_coord or "")
    amap_key, amap_key_from = resolve_amap_key(args)
    prefer = args.prefer
    modes = [m.strip() for m in (args.modes or "").split(",") if m.strip()] or list(MODES)
    unknown = [m for m in modes if m not in MODES]
    if unknown:
        warnings.append(f"未知方式已忽略：{', '.join(unknown)}（可选 {', '.join(MODES)}）")
    modes = [m for m in modes if m in MODES] or list(MODES)

    entries: list[dict] = []
    result: dict[str, Any] = {
        "from": args.from_name or args.from_place or args.from_coord or "A",
        "to": args.to_name or args.to_place or args.to_coord or "B",
        "date": args.date,
        "prefer": prefer,
        "status": "",
        "summary": "",
        "warnings": warnings,
        "assumptions": assumptions,
        "next_action": "",
        "failure_category": None,
        "rail": {"available": False},
        "transit": {"available": False},
        "taxi": {"available": False},
        "drive": {"available": False},
        "walk": {"available": False},
        "amap": {"key": bool(amap_key), "key_source": amap_key_from or None},
        "comparison": entries,
    }

    # ---- 铁路 ----
    if "rail" in modes:
        if not rail_script:
            warnings.append(f"未找到 rail-transfer 脚本（期望 {DEFAULT_RAIL_SCRIPT}），铁路不可用")
        elif not (args.from_name and args.to_name):
            warnings.append("铁路需要站名/城市名（--from/--to）；只给坐标时无法查铁路")
        else:
            rail = call_rail(rail_script, args.from_name, args.to_name, args.date,
                             args.rail_min_transfer, args.rail_via,
                             args.rail_arg, args.rail_timeout)
            if not rail.get("ok"):
                warnings.append(rail.get("summary") or "rail-transfer 调用失败")
                result["next_action"] = "检查 rail-transfer skill 环境（--detect）后重试"
            else:
                options = normalize_rail(rail)
                data = rail["data"]
                result["rail"] = {
                    "available": True,
                    "status": rail["status"],
                    "summary": rail["summary"],
                    "from": data.get("from"),
                    "to": data.get("to"),
                    "options": options[:8],
                }
                if not options:
                    warnings.append(
                        f"铁路没有可用方案：{rail.get('summary') or ''}")
                    result["rail"]["needs_confirmation"] = rail.get("evidence") or []
                    if rail.get("failure_category") == FC_AMBIG:
                        result["status"] = "blocked"
                        result["failure_category"] = FC_AMBIG
                        result["next_action"] = rail.get("next_action") or \
                            "城市名有多个车站，先选定具体车站"
    # ---- 高德：连通性、坐标、城市、驾车路线 ----
    amap = None
    amap_error = ""
    if amap_key:
        amap = make_amap(amap_key)
        try:
            amap.check()
        except amap_client.AmapError as exc:
            amap_error = amap_unavailable_reason(exc)
            warnings.append(f"{amap_error}；本次退回估算")
            amap = None
    need_coord = any(m in modes for m in ("taxi", "drive", "transit", "walk"))
    if amap:
        if not from_coord and args.from_place:
            from_coord = resolve_place(amap, args.from_place, args.from_city, warnings, "起点")
        if not to_coord and args.to_place:
            to_coord = resolve_place(amap, args.to_place, args.to_city, warnings, "终点")
    if need_coord and not (from_coord and to_coord):
        # 不拿地名去猜坐标：站名/城市名地理编码偏差能有十几公里，宁可只算铁路
        warnings.append("公交/打车/自驾/步行需要坐标（--from-coord/--to-coord lat,lng）；"
                        "只给名字时这些方式不参与比较")

    city_a = args.from_city or (amap.city_of(from_coord) if amap and from_coord else None)
    city_b = args.to_city or (amap.city_of(to_coord) if amap and to_coord else None)

    straight = haversine_km(from_coord, to_coord) if (from_coord and to_coord) else None
    driving = None
    if amap and from_coord and to_coord:
        try:
            driving = amap.driving(from_coord, to_coord)
        except amap_client.AmapError as exc:
            warnings.append(f"高德驾车规划失败：{amap_unavailable_reason(exc)}")

    if straight is not None:
        est_km, est_min = estimate_distance(cfg, straight)
        if driving and driving.get("distance_km"):
            dist_km = driving["distance_km"]
            dur_min = driving.get("duration_min") or est_min
            road_source = "高德驾车路线"
        else:
            dist_km, dur_min = est_km, est_min
            road_source = "估算（直线 × 绕行系数）"
        city = args.taxi_city or city_b or city_a or args.to_name or args.from_name or ""
        city_name, rule = pick_city_rule(cfg, city)
        pickup = int(cfg["taxi"].get("pickup_min", 0) or 0)

        if "taxi" in modes:
            amap_fare = (driving or {}).get("taxi_cost")
            if amap_fare:
                fare = float(amap_fare)
                taxi_detail = (f"高德驾车 {dist_km:.1f} km / {_fmt_min(dur_min)}；"
                               f"含等车 {pickup} 分；打车费取高德路线估价 ¥{fare:g}")
                taxi_note = ("高德按出租车计价规则估的打车费，网约车实际有浮动/优惠；"
                             "以下单页为准")
                taxi_conf = "高德估价"
            else:
                fare = taxi_fare(rule, dist_km)
                taxi_detail = (f"直线 {straight:.1f} km → {road_source} {dist_km:.1f} km / "
                               f"{_fmt_min(dur_min)}；含等车 {pickup} 分；计价：{city_name}规则 "
                               f"（起步 ¥{rule['base_fare']:g}/{rule['base_km']:g}km，"
                               f"¥{rule['per_km']:g}/km）")
                taxi_note = "按计价规则估算（没有高德 Key），实际以下单页为准"
                taxi_conf = "估算"
            if dist_km > float(cfg["taxi"].get("intercity_threshold_km", 30)):
                taxi_note += "；跨城打车通常还有返程/空驶费，实际会明显高于此价"
            entries.append({
                "mode": "taxi",
                "name": "全程打车",
                "total_min": dur_min + pickup,
                "cost": round(fare, 1),
                "confidence": taxi_conf,
                "detail": taxi_detail,
                "note": taxi_note,
            })
            result["taxi"] = {"available": True, "distance_km": round(dist_km, 1),
                              "duration_min": dur_min, "cost": round(fare, 1),
                              "source": road_source,
                              "fare_source": "高德 taxi_cost" if amap_fare else "计价规则"}

        if "drive" in modes:
            real_tolls = (driving or {}).get("tolls")
            dcost = drive_cost(cfg, dist_km, real_tolls)
            if real_tolls is not None:
                toll_note = (f"过路费取高德实测 ¥{real_tolls:g}" if real_tolls
                             else "高德路线无过路费（走的是免费道路）")
            else:
                toll_note = (f"过路费按 ¥{cfg['drive']['toll_per_km']:g}/km×"
                             f"{cfg['drive']['highway_ratio']:g} 估算")
            entries.append({
                "mode": "drive",
                "name": "自驾",
                "total_min": dur_min,
                "cost": round(dcost, 1),
                "confidence": "高德路线 + 油耗假设" if driving else "估算",
                "detail": f"{road_source} {dist_km:.1f} km / {_fmt_min(dur_min)}；"
                          f"油/电 ¥{cfg['drive']['fuel_cost_per_km']:g}/km + {toll_note}",
                "note": "未含停车费；油/电成本按车型可自行改 costs.json",
            })
            result["drive"] = {"available": True, "distance_km": round(dist_km, 1),
                               "duration_min": dur_min, "cost": round(dcost, 1),
                               "tolls": real_tolls, "source": road_source}

        # ---- 公共交通（地铁/公交/步行，含跨城） ----
        if "transit" in modes:
            if not amap:
                warnings.append(f"公共交通不可用：{amap_error}" if amap_error
                                else "公共交通需要高德 Key（没有 Key 查不到真实公交线路）")
            elif not city_a:
                warnings.append("公共交通需要起点城市（--from-city 或高德逆地理编码）")
            else:
                try:
                    plans = amap.transit(from_coord, to_coord, city_a, city_b,
                                         getattr(args, "transit_strategy", 0))
                except amap_client.AmapError as exc:
                    plans = []
                    warnings.append(f"高德公交规划失败：{amap_unavailable_reason(exc)}")
                plan = pick_transit_plan(plans, prefer)
                if not plan:
                    warnings.append("高德没有返回公交方案（可能距离太远/跨城无公交数据）")
                else:
                    name = "公共交通"
                    if plan.get("has_subway"):
                        name = "公共交通（含地铁）"
                    elif plan.get("has_rail"):
                        name = "公共交通（含火车）"
                    legs = plan.get("legs") or []
                    ride = [leg for leg in legs if leg["kind"] in ("bus", "rail")]
                    walk_legs = [leg for leg in legs if leg["kind"] == "walk"]
                    detail = (f"{len(ride)} 段乘车 + {len(walk_legs)} 段步行，"
                              f"步行共 {plan.get('walk_m') or 0} 米"
                              f"（约 {plan.get('walk_min') or 0} 分）")
                    note = "逐段见下；票价取高德返回，实际以站点闸机为准"
                    entries.append({
                        "mode": "transit",
                        "name": name,
                        "total_min": plan.get("duration_min"),
                        "cost": plan.get("cost"),
                        "confidence": "高德实测",
                        "detail": detail,
                        "note": note,
                        "legs": legs,
                    })
                    result["transit"] = {
                        "available": True,
                        "duration_min": plan.get("duration_min"),
                        "cost": plan.get("cost"),
                        "walk_m": plan.get("walk_m"),
                        "walk_min": plan.get("walk_min"),
                        "walk_legs": walk_legs,
                        "legs": legs,
                        "plans_found": len(plans),
                    }

        # ---- 纯步行（短距离才有意义） ----
        walk_cfg = cfg.get("walk") or {}
        walk_limit = float(walk_cfg.get("max_km", 5))
        if "walk" in modes and straight is not None and straight <= walk_limit:
            walk = None
            if amap:
                try:
                    walk = amap.walking(from_coord, to_coord)
                except amap_client.AmapError as exc:
                    warnings.append(f"高德步行规划失败：{amap_unavailable_reason(exc)}")
            if walk and walk.get("duration_min"):
                detail = f"步行 {walk.get('distance_km')} km / {_fmt_min(walk['duration_min'])}"
                conf = "高德实测"
            else:
                wkm = straight * float(walk_cfg.get("detour_factor", 1.25))
                wmin = walk_minutes(cfg, wkm)
                detail = f"直线 {straight:.1f} km → 估算步行 {wkm:.1f} km / {_fmt_min(wmin)}"
                conf = "估算"
                walk = {"distance_km": round(wkm, 1), "duration_min": wmin}
            steps = [s for s in (walk.get("steps") or []) if s.get("instruction")]
            walk_note = "免费；超过 5 km 一般不建议步行（可用 --modes 关掉）"
            if steps:
                head = "；".join(
                    f"{s['instruction']}（{s['distance_m']} 米）" for s in steps[:3])
                walk_note = f"步行指引：{head}" + ("…" if len(steps) > 3 else "") + "；" + walk_note
            entries.append({
                "mode": "walk",
                "name": "全程步行",
                "total_min": walk.get("duration_min"),
                "cost": 0.0,
                "confidence": conf,
                "detail": detail,
                "note": walk_note,
            })
            result["walk"] = {"available": True, "distance_km": walk.get("distance_km"),
                              "duration_min": walk.get("duration_min"),
                              "steps": steps}

        if "taxi" in modes or "drive" in modes:
            assumptions.append(road_source)
        assumptions.append(f"坐标：A {from_coord}，B {to_coord}"
                           + (f"；城市：{city_a}→{city_b}" if city_a else ""))
    # ---- 铁路门到门 ----
    if result["rail"].get("available") and result["rail"].get("options"):
        picked = select_rail_options(result["rail"]["options"], prefer,
                                     getattr(args, "max_rail_options", 2) or 2)
        station_from = picked[0].get("from_station") if picked else None
        station_to = picked[0].get("to_station") if picked else None
        acc_o, acc_d = resolve_access(
            cfg, args, amap, from_coord, to_coord, station_from, station_to,
            city_a, city_b, warnings, key_configured=bool(amap_key))
        for opt in picked:
            total = (opt.get("total_min") or 0) + acc_o["minutes"] + acc_d["minutes"]
            cost = opt.get("cost")
            entries.append({
                "mode": "rail",
                "name": (f"直达 {opt['legs'][0]}（门到门）" if opt["kind"] == "direct"
                         else f"高铁 {'+'.join(opt['legs'])}（门到门）"),
                "total_min": total,
                "cost": None if cost is None else round(cost + acc_o["cost"] + acc_d["cost"], 1),
                "confidence": "铁路实测 + 接驳高德" if amap else "铁路实测 + 接驳假设",
                "detail": f"{opt['label']}；接驳 A→站 {acc_o['minutes']}分/"
                          f"¥{acc_o['cost']:g}（{acc_o['source']}"
                          f"{'；' + acc_o['alt'] if acc_o.get('alt') else ''}），"
                          f"站→B {acc_d['minutes']}分/¥{acc_d['cost']:g}"
                          f"（{acc_d['source']}"
                          f"{'；' + acc_d['alt'] if acc_d.get('alt') else ''}）",
                "note": f"铁路 {_fmt_min(opt.get('total_min'))} / "
                        f"{_fmt_money(opt.get('cost'))}；"
                        f"接驳来源：{acc_o['source']} + {acc_d['source']}",
            })
        if getattr(args, "no_access", False):
            assumptions.append("接驳：按人已经在车站计算（两端各 0 分钟 / ¥0）")
        else:
            assumptions.append(
                f"接驳：A→出发站 {acc_o['minutes']} 分钟 / ¥{acc_o['cost']:g}"
                f"（{acc_o['source']}）；到达站→B {acc_d['minutes']} 分钟 / "
                f"¥{acc_d['cost']:g}（{acc_d['source']}）")
            for side in (acc_o, acc_d):
                if side.get("detail"):
                    assumptions.append(f"接驳线路：{side['detail']}")
        result["access"] = {"origin": acc_o, "destination": acc_d}

    # ---- 汇总 ----
    if entries:
        ordered = build_trends(entries, prefer)
        result["comparison"] = ordered
        if result.get("status") == "blocked":
            for e in ordered:
                e["recommended"] = False
            result["summary"] = ("铁路站点需要先确认，"
                                 "下表只有公共交通/打车/自驾/步行，暂不推荐")
            if not result.get("next_action"):
                result["next_action"] = "先选定具体车站（见候选车站），再重跑比较"
        else:
            best = next((e for e in ordered if e.get("recommended")), ordered[0])
            result["status"] = "success"
            result["summary"] = (f"{len(ordered)} 种方式可比；按 {prefer} 推荐"
                                 f"「{best['name']}」：{_fmt_min(best['total_min'])}，"
                                 f"{_fmt_money(best['cost'])}")
            result["recommended"] = best
    elif result.get("status") == "blocked":
        result["summary"] = "铁路站点有歧义，需要先选定具体车站"
    else:
        result["status"] = "failure"
        result["failure_category"] = result.get("failure_category") or FC_ENV
        result["summary"] = "没有可用数据：需要站名（铁路）或坐标/高德 Key（公交/打车/自驾/步行）"
        result["next_action"] = ("给 --from/--to 站名，或给 --from-coord/--to-coord 经纬度；"
                                 "公共交通/打车建议配置高德 Key（AMAP_KEY）")
    return result


# --- 入口 ------------------------------------------------------------------
def cmd_detect(args) -> int:
    print("=== trip-planner 环境自检 ===")
    rail_script = find_rail_script(args.rail_script)
    print(f"python            : {sys.version.split()[0]}")
    print(f"rail-transfer 脚本 : {rail_script or '未找到'}")
    if rail_script:
        try:
            proc = subprocess.run(  # noqa: S603  # 只跑本地 rail-transfer 脚本
                [sys.executable, str(rail_script), "--detect"],
                check=False, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
            out = (proc.stdout or "").strip().splitlines()
            tail = ""
            for line in out:
                if "结果" in line or "✅" in line or "⚠️" in line:
                    tail = line.strip()
            if not tail:
                tail = out[-1].strip() if out else ""
            print(f"rail-transfer 自检 : {tail or '(无输出)'}")
        except Exception as exc:  # noqa: BLE001
            print(f"rail-transfer 自检 : 失败 {exc}")
    cfg = _load_json(SKILL_DIR / "references" / "costs.json")
    print(f"计价配置          : {len(cfg.get('taxi', {}).get('cities') or {})} 个城市 + 默认")
    key, key_from = resolve_amap_key(args)
    if not key:
        print("高德 Key          : 未配置 → 公交/打车/步行只能用估算")
        print("                    申请：https://console.amap.com/dev/key/app "
              "（服务平台选「Web服务」）")
        print("                    填法：setx AMAP_KEY \"<key>\" 或写进 references/amap.json")
    else:
        try:
            city = make_amap(key, timeout=15).check()
            print(f"高德 Key          : ✅ 有效（来源 {key_from}；逆地理编码自检返回 {city}）")
        except amap_client.AmapError as exc:
            print(f"高德 Key          : ❌ {amap_unavailable_reason(exc)}")
    print("结果：✅ 就绪" if rail_script else "结果：⚠️ 铁路不可用，请先修 rail-transfer skill")
    return 0 if rail_script else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trip_planner.py",
        description="比较 A→B 的出行方式：铁路（rail-transfer）+ 公共交通 / 打车 / 自驾 / 步行（高德）")
    p.add_argument("--version", action="version", version=f"trip-planner {VERSION}")
    p.add_argument("--detect", action="store_true", help="环境自检（含高德 Key 连通性）")
    p.add_argument("--from", dest="from_name", help="A 点：站名/城市名（用于铁路，也可给高德地理编码）")
    p.add_argument("--to", dest="to_name", help="B 点：站名/城市名")
    p.add_argument("--from-coord", help="A 点坐标 lat,lng（公交/打车/自驾/步行必需）")
    p.add_argument("--to-coord", help="B 点坐标 lat,lng")
    p.add_argument("--from-place", help="A 点地址/店名，用高德 POI 搜索换成坐标")
    p.add_argument("--to-place", help="B 点地址/店名，用高德 POI 搜索换成坐标")
    p.add_argument("--from-city", help="A 点城市名（公共交通起点城市；默认逆地理编码猜）")
    p.add_argument("--to-city", help="B 点城市名（跨城公交的 cityd）")
    p.add_argument("--date", help="乘车日期 YYYY-MM-DD（默认明天）")
    p.add_argument("--prefer", choices=["time", "price", "balance"], default="balance",
                   help="推荐偏好：time 最快 / price 最便宜 / balance 综合（默认）")
    p.add_argument("--modes", default="rail,transit,taxi,drive,walk",
                   help="要比较的方式，逗号分隔（默认 rail,transit,taxi,drive,walk）")
    p.add_argument("--access", choices=["auto", "assume"], default="auto",
                   help="接驳怎么算：auto=高德公交/打车规划（默认，没 Key 时自动退回假设），"
                        "assume=直接用假设值")
    p.add_argument("--access-mode", choices=["fastest", "transit", "taxi"], default="fastest",
                   help="接驳方式：fastest=公交与打车取更快（默认）/ transit=只用公交 / taxi=只打车")
    p.add_argument("--no-access", action="store_true",
                   help="两端接驳按 0 分钟 / ¥0 计（人本来就站在车站时用）")
    p.add_argument("--access-origin-min", type=int, help="A→出发站 接驳分钟数（覆盖自动计算）")
    p.add_argument("--access-origin-cost", type=float, help="A→出发站 接驳费用")
    p.add_argument("--access-dest-min", type=int, help="到达站→B 接驳分钟数")
    p.add_argument("--access-dest-cost", type=float, help="到达站→B 接驳费用")
    p.add_argument("--transit-strategy", type=int, default=0, choices=[0, 1, 2, 3, 5],
                   help="高德公交策略：0 最快捷 / 1 最经济 / 2 最少换乘 / 3 最少步行 / 5 不坐地铁")
    p.add_argument("--rail-min-transfer", type=int, default=16, help="铁路换乘缓冲（默认 16）")
    p.add_argument("--rail-via", help="铁路中转站/城市")
    p.add_argument("--rail-arg", action="append", default=[],
                   help="额外传给 rail-transfer 的参数，可重复（如 --rail-arg=--seat-class=二等座）")
    p.add_argument("--rail-script", help="rail-transfer 脚本路径（默认自动找同级 skill）")
    p.add_argument("--rail-timeout", type=int, default=600, help="铁路查询超时秒数")
    p.add_argument("--max-rail-options", type=int, default=2,
                   help="对比表里最多展示几趟铁路方案（默认 2，全集仍在 JSON 的 data.rail.options）")
    p.add_argument("--amap-key", help="高德「Web服务」Key（或用环境变量 AMAP_KEY）")
    p.add_argument("--taxi-city", help="打车计价规则城市（默认按 B 点/配置猜）")
    p.add_argument("--format", choices=["text", "json"], default="text")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.detect:
        return cmd_detect(args)
    if not args.date:
        args.date = (_date.today() + timedelta(days=1)).isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
        print(json.dumps({"status": "failure", "failure_category": FC_INPUT,
                          "summary": f"日期格式不对：{args.date}"},
                         ensure_ascii=False, indent=2))
        return 1
    cfg = _load_json(SKILL_DIR / "references" / "costs.json")
    result = build_result(args, cfg)
    out = {
        "status": result["status"],
        "summary": result["summary"],
        "evidence": _evidence(result),
        "next_action": result.get("next_action", ""),
        "failure_category": result.get("failure_category"),
        "data": result,
    }
    if args.format == "json":
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(render_text(out))
    return {"success": 0, "blocked": 2}.get(out["status"], 1)


def _evidence(result: dict) -> list[str]:
    ev: list[str] = []
    rail = result.get("rail") or {}
    if rail.get("available"):
        ev.append(f"铁路：{rail.get('status')} · {rail.get('summary')}")
    else:
        ev.append("铁路：不可用")
    if (result.get("transit") or {}).get("available"):
        tr = result["transit"]
        ev.append(f"公共交通：{tr.get('duration_min')} 分 / "
                  f"{_fmt_money(tr.get('cost'))} / 步行 {tr.get('walk_m') or 0} 米"
                  f"（{tr.get('plans_found')} 条方案）")
    if (result.get("taxi") or {}).get("available"):
        ev.append(f"打车：{result['taxi']['distance_km']} km / "
                  f"{result['taxi']['duration_min']} 分 / ¥{result['taxi']['cost']:g}"
                  f"（{result['taxi'].get('fare_source')}）")
    if (result.get("drive") or {}).get("available"):
        ev.append(f"自驾：{result['drive']['distance_km']} km / "
                  f"{_fmt_money(result['drive']['cost'])}")
    if (result.get("walk") or {}).get("available"):
        ev.append(f"步行：{result['walk'].get('distance_km')} km / "
                  f"{result['walk'].get('duration_min')} 分")
    ev.extend(result.get("warnings") or [])
    ev.extend((result.get("rail") or {}).get("needs_confirmation") or [])
    return ev


if __name__ == "__main__":
    sys.exit(main())
