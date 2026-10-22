# 🧭 trip-planner · 出行方式对比

> 给一个 A 点和一个 B 点，它把**高铁/火车、公交地铁、打车、自驾、步行**摆到一张表里，算**门到门**的时间与费用，再按「最快 / 最便宜 / 综合」给出推荐。
>
> 铁路数据来自 12306（复用 [rail-transfer](https://github.com/qijie233/china-rail-transfer)），公交/打车/自驾/步行来自高德 Web 服务。**该实测的标实测，该估算的标估算**，不把估价写成实价。

<p align="center">
  <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/qijie233/china-trip-planner/ci.yml?label=CI&logo=github">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg">
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB.svg?logo=python&logoColor=white">
  <img alt="Zero deps" src="https://img.shields.io/badge/runtime%20deps-0-brightgreen.svg">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg">
</p>

```
🧭 句容 → 南京南    2026-10-24    偏好：balance

━━ 方式对比 ━━
 1. 直达 G3272（门到门）   总耗时 35分   总费用 ¥32   [铁路实测 + 接驳高德]  ← 推荐
    G3272 句容 10:21 → 南京南 10:38；接驳 A→站 13分/¥14（高德打车；高德公交 38 分 / ¥1），
    站→B 5分/¥0（高德步行（就在站旁））
 2. 自驾   总耗时 55分   总费用 ¥28   [高德路线 + 油耗假设]
    高德驾车路线 46.6 km / 55分；油/电 ¥0.6/km + 高德路线无过路费（走的是免费道路）
 3. 公共交通（含地铁）   总耗时 1小时55分   总费用 ¥9   [高德实测]
    3 段乘车 + 3 段步行，步行共 1993 米（约 32 分）
      1. 🚶 步行 1291 米（约 20 分） 到 2号口 进站
      2. 🚇 地铁S6号线(宁句线)(句容--马群)  崇明→马群（10 站，43 分）
      3. 🚶 步行 379 米（约 7 分）：步行91米到达马群
      4. 🚇 地铁2号线(经天路--鱼嘴)  马群→明故宫（4 站，14 分）
      5. 🚶 步行 323 米（约 5 分）：步行200米到达明故宫
      6. 🚇 地铁6号线(栖霞山--南京南站)  明故宫→南京南站（6 站，18 分），8号口 出站
 4. 全程打车   总耗时 1小时00分   总费用 ¥126   [高德估价]
    高德驾车 46.6 km / 55分；含等车 5 分；打车费取高德路线估价 ¥126

状态：success · 4 种方式可比；按 balance 推荐「直达 G3272（门到门）」：35分，¥32
```

## ✨ 它解决什么

大多导航 App 只给「站到站」，而真正决定你几点出门、花多少钱的是**门到门**：

- 家到车站这段怎么走、多久、多少钱？
- 中间高铁要不要换乘、等多久？
- 下车后还要走多远、要不要打车？
- 全打车 / 自己开车，是不是反而更快更便宜？

trip-planner 把这些拼成一条可比较的链路，并明确标注每一段的数据来源。

| 能力 | 说明 |
|---|---|
| 🚄 铁路 | 直达 + 自动中转换乘（换乘缓冲、同城异站通勤都算），票价按 12306 |
| 🚇 公共交通 | 高德公交路径规划：地铁/公交/跨城火车，**逐段列出**，含票价 |
| 🚶 步行段 | 每段距离、时长、进站口/出站口；≤5 km 还能单列「全程步行」 |
| 🚕 打车 | 高德驾车路线给里程/时长，车费取高德 `taxi_cost` 估价 |
| 🚗 自驾 | 真实路线的实测过路费 + 可配置油电成本 |
| 🚉 接驳 | 默认走高德公交/打车，取更快的一条；站点就在旁边就按步行算 |
| ⚖️ 推荐 | 按 `time` / `price` / `balance` 排序，平手优先更快 |
| 🧩 降级 | 没有高德 Key 也能用：公交/打车退回估算并明确告警，绝不假装是实测 |

## 🚀 快速开始

### 1. 放到 skills 目录

```bash
# 和 rail-transfer 放成兄弟目录（trip-planner 会自动找到它）
cd ~/.cc-switch/skills        # Windows: C:\Users\<你>\.cc-switch\skills
git clone https://github.com/qijie233/china-trip-planner.git trip-planner
git clone https://github.com/qijie233/china-rail-transfer.git rail-transfer
```

> Windows 的铁路查询依赖 Edge/Chrome + `websocket-client`，由 rail-transfer 负责。
> 不想用技能目录也行：任何地方直接 `python trip-planner/scripts/trip_planner.py`。

### 2. 申请高德 Key（免费，3 分钟）

1. 打开 <https://console.amap.com/dev/key/app>，注册/登录高德开放平台开发者
2. 「应用管理 → 创建新应用」→ 在新应用里「添加 Key」
3. **服务平台必须选「Web服务」**（选成 JS / Android 会报 10009）
4. 填给脚本，三种方式任选：

```bash
export AMAP_KEY="你的32位Key"          # 方式一：环境变量
```

```jsonc
// 方式二：references/amap.json（已在 .gitignore，不会误提交）
{ "key": "你的32位Key" }
```

```bash
python scripts/trip_planner.py --amap-key 你的32位Key ...   # 方式三：单次传入
```

没有 Key 也能跑，只是公交/打车/步行会变成估算（会告警）。

### 3. 自检

```bash
python scripts/trip_planner.py --detect
```

```
=== trip-planner 环境自检 ===
python            : 3.13.15
rail-transfer 脚本 : ...\rail-transfer\scripts\rail_transfer.py
rail-transfer 自检 : 结果: ✅ 环境就绪，可以开始查询。
计价配置          : 6 个城市 + 默认
高德 Key          : ✅ 有效（来源 references/amap.json；逆地理编码自检返回 北京市）
结果：✅ 就绪
```

## 📖 用法

```bash
# 最省事：直接给地址，用高德 POI 搜索换坐标
python scripts/trip_planner.py --from 句容 --to 昆山南 \
  --from-place 句容雅居乐花园 --to-place 花香里营地 \
  --date 2026-10-24

# 最准：直接给坐标
python scripts/trip_planner.py --from 句容 --to 南京南 \
  --from-coord 31.95,119.17 --to-coord 31.968,118.797 \
  --prefer time

# 只要公共交通（含步行段）
python scripts/trip_planner.py --from-coord 31.95,119.17 --to-coord 31.41,121.13 \
  --modes transit --transit-strategy 3     # 3 = 最少步行

# 给脚本用
python scripts/trip_planner.py --from 句容 --to 太仓 --from-place 句容雅居乐花园 \
  --to-place 苏州健雄职业技术学院 --format json | jq .summary
```

### 参数

| 参数 | 说明 |
|---|---|
| `--from` / `--to` | 站名或城市名（铁路用）。多车站城市会返回 `blocked` + 候选站清单 |
| `--from-coord` / `--to-coord` | 坐标 `lat,lng`，公交/打车/自驾/步行必需 |
| `--from-place` / `--to-place` | 地址/店名，用高德 POI 搜索换坐标 |
| `--from-city` / `--to-city` | 城市名，跨城公交的 `city` / `cityd` |
| `--date` | `YYYY-MM-DD`，默认明天 |
| `--prefer` | `time` 最快 / `price` 最便宜 / `balance` 综合（默认） |
| `--modes` | `rail,transit,taxi,drive,walk`，默认全开（步行 >5 km 自动跳过） |
| `--access` | `auto`（默认，高德算接驳）/ `assume`（用假设值） |
| `--access-mode` | `fastest`（公交打车取更快）/ `transit` 只公交 / `taxi` 只打车 |
| `--no-access` | 人就在车站，两端 0 分 / ¥0 |
| `--access-origin-min/cost`、`--access-dest-min/cost` | 手动指定接驳 |
| `--transit-strategy` | `0` 最快捷 / `1` 最经济 / `2` 最少换乘 / `3` 最少步行 / `5` 不坐地铁 |
| `--rail-via` | 指定中转站 |
| `--rail-min-transfer` | 最小换乘缓冲（默认 16 分钟） |
| `--max-rail-options` | 对比表里列几趟车（默认 2，全集在 JSON） |
| `--rail-arg` | 透传给 rail-transfer，可重复（如 `--rail-arg=--no-cache`） |
| `--amap-key` | 单次指定高德 Key |
| `--format` | `text`（默认）/ `json` |
| `--detect` / `--version` | 自检 / 版本 |

### 输出约定

人读报告 = 对比表 + 逐段明细 + 假设与来源 + 告警。

`--format json` 固定信封：

```jsonc
{
  "status": "success",          // success | blocked | failure
  "summary": "5 种方式可比；按 balance 推荐「直达 G3272（门到门）」：35分，¥32",
  "evidence": ["铁路：success · 15 趟直达…", "公共交通：115 分 / ¥9 / 步行 1993 米"],
  "next_action": "",
  "failure_category": null,     // environment-missing | ambiguous-context | connection-failure | invalid-input
  "data": {
    "comparison": [ { "mode": "rail", "total_min": 35, "cost": 32, "confidence": "铁路实测 + 接驳高德", "recommended": true, "legs": [] } ],
    "rail":    { "options": [ ... ] },
    "transit": { "walk_m": 1993, "walk_legs": [ ... ], "legs": [ { "kind": "bus", "text": "…" } ] },
    "access":  { "origin": { "source": "高德打车", "minutes": 13, "cost": 14 }, "destination": { ... } }
  }
}
```

退出码：`0` 成功 · `2` 站点歧义（需先选站） · `1` 失败。

## 🧠 工作原理

```
        ┌──────────────┐        subprocess         ┌────────────────────┐
 A点 ──▶│ 高德 路径规划 │◀──────────────────────────│ trip_planner.py    │
        │ 公交/步行/驾车│                            │  ① 归一化各段      │
        └──────────────┘                            │  ② 拼门到门        │──▶ 对比表 / JSON
        ┌──────────────┐        subprocess         │  ③ 按偏好排序      │
 站名 ──▶│ rail-transfer │◀──────────────────────────│  ④ 生成告警/假设   │
        │ (12306 余票票价)│                           └────────────────────┘
        └──────────────┘
```

- **不重复实现**：12306 的一切交给 rail-transfer（中转、缓冲、余票、票价、缓存）。
- **不猜坐标**：站名/城市名不会硬当地址用（地理编码能偏十几公里），要地址就 `--from-place`，要精确就 `--from-coord`。
- **空结果自动重查**：rail-transfer 缓存里若留着「没车」，会自动加 `--no-cache` 再查一次。
- **纯标准库**：运行时不依赖任何第三方包（`urllib` 直接调高德）。

## 🔍 数据来源与可信度

| 方式 | 来源 | 可信度 |
|---|---|---|
| 铁路 | rail-transfer → 12306 | **实测**（余票/票价动态） |
| 公共交通 | 高德 `v3/direction/transit/integrated` | **实测**（含跨城，跨城方案里的火车段带车次时刻） |
| 步行 | 高德 `v3/direction/walking` | 实测；无 Key 按 4.8 km/h 估算 |
| 打车 | 高德驾车路线 + `route.taxi_cost` | **估价**（出租车计价），非网约车实时价 |
| 自驾 | 高德驾车路线（实测过路费）+ 油电假设 | 路线实测，成本含假设 |
| 接驳 | 高德公交/打车；无 Key 退回 `references/costs.json` | 优先实测 |

**已知限制**（不会藏着）：

- 门到门总时长**不含候车时间**（不知道你几点出门）。
- 打车价是出租车口径的估价，滴滴/高德网约车有浮动，跨城常加返程费。
- 高德时长含实时路况，同一天不同时段跑结果会变。

## ⚙️ 可调参数

`references/costs.json`：

```jsonc
{
  "access": { "origin": { "minutes": 30, "cost": 30 }, "destination": { "minutes": 30, "cost": 30 } },
  "taxi":   { "pickup_min": 5, "detour_factor": 1.4, "city_speed_kmh": 26,
              "intercity_speed_kmh": 55, "long_speed_kmh": 80,
              "cities": { "南京": { "base_fare": 11, "base_km": 3, "per_km": 2.4 } } },
  "drive":  { "fuel_cost_per_km": 0.6, "toll_per_km": 0.5, "highway_ratio": 0.8 },
  "walk":   { "speed_kmh": 4.8, "max_km": 5 }
}
```

## 🧪 测试

完全不联网（高德返回用 fixture 造假数据）：

```bash
python -m pytest -q            # 54 个单元测试
python scripts/selftest.py     # 高德解析离线自检（技能里可直接跑）
python -m ruff check .         # 代码风格
```

CI 在 Ubuntu + Windows、Python 3.10 / 3.13 四组组合上跑上面三件事。

## ❓ FAQ

**Q：为什么门到门时间没算等车？**
A：不知道你几点出门。要算候车请给出发时间或指定车次，再自己加上「到站时间 − 出发时间」。

**Q：为什么不直接用高德给全程方案就完事？**
A：高德不查 12306。跨城长途高铁的余票、票价、换乘缓冲必须从 12306 拿，才可能同时给出「票还有没有」和「换乘来不来得及」。

**Q：报 `INVALID_USER_KEY` / `10009`？**
A：前者 Key 填错或没生效；后者 Key 的「服务平台」不是 Web服务，去控制台改。

**Q：铁路明明有车却报无方案？**
A：老版本的余票缓存问题，现在会自动 `--no-cache` 重查一次；仍不行就手动 `--rail-arg=--no-cache`。

## 📄 License

[MIT](LICENSE) © 2026 祈杰 (qijie233)

铁路查询部分来自姊妹项目 [china-rail-transfer](https://github.com/qijie233/china-rail-transfer)（MIT）。
高德数据版权归 [高德开放平台](https://lbs.amap.com/) 所有，请遵守其服务条款与配额限制。
