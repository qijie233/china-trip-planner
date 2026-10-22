---
name: trip-planner
description: 当需要比较从 A 点到 B 点的出行方式（铁路/高铁、公共交通公交地铁、打车、自驾、步行），计算门到门时间与费用、按到达时间/价格挑方案时使用。适用场景：A 到 B 有直达或中转火车可选，同时想知道公共交通/打车/自驾是否更划算；需要真实公交地铁换乘与步行路段；有最早到达、最便宜、总时长别太长等偏好。
---

# 出行方式对比（铁路 + 公共交通 + 打车 / 自驾 / 步行）

## 适用场景

- 需要从 A 点到 B 点，想同时看**铁路（含中转）**、**公共交通（公交/地铁）**、
  **全程打车**、**自驾**、**步行**的时间与费用。
- 想知道「这段到底要不要坐地铁」「最后一公里走多久」——公共交通方案里会**逐段列出步行**。
- 已知偏好：最早到达 / 最便宜 / 综合性价比 / 总时长别太长。
- 想算**门到门**：A→车站 + 铁路 + 车站→B，接驳默认也走高德公交规划而不是拍脑袋。

## 必要输入

- `--from` / `--to`：站名或城市名（铁路用；城市名多站时会返回候选站让你先选）
- `--from-coord` / `--to-coord`：坐标 `lat,lng`，**公交/打车/自驾/步行必需**。
  不给坐标时这些方式不参与比较（不再拿地名去猜坐标——站名地理编码偏差能有十几公里）
- `--from-place` / `--to-place`：地址/店名（如「句容雅居乐花园」「花香里营地」），
  用高德 POI 关键字搜索换坐标；比按地名地理编码准得多，找不到会告警
- `--from-city` / `--to-city`：起点/终点城市名，跨城公交（`city`/`cityd`）用；省略时用高德逆地理编码从坐标猜
- `--date`：`YYYY-MM-DD`，省略默认明天
- `--prefer time|price|balance`：推荐偏好，默认 `balance`
- `--amap-key` 或环境变量 `AMAP_KEY`：**公共交通/打车费/步行全靠它**（见下）
- 可选：
  - `--access auto|assume`：接驳怎么算。`auto`（默认）用高德规划算 A→站、站→B；`assume` 直接用固定假设
  - `--access-mode fastest|transit|taxi`：接驳方式。`fastest`（默认）公交与打车取更快的一条，
    `transit` 只坐公交地铁，`taxi` 只打车（站旁 0.4 km 内自动按步行）
  - `--no-access` 人已在车站（两端 0 分钟 / ¥0）；`--access-origin-min/cost`、`--access-dest-min/cost` 手动覆盖
  - `--transit-strategy 0|1|2|3|5`：高德公交策略（0 最快捷 / 1 最经济 / 2 最少换乘 / 3 最少步行 / 5 不坐地铁）
  - `--modes rail,transit,taxi,drive,walk`：只比其中几种（默认全开；步行超过 5 km 会自动跳过）
  - `--max-rail-options N`：对比表里最多列几趟铁路方案（默认 2，全集在 JSON `data.rail.options`）
  - `--taxi-city`：打车计价规则城市（高德没给 `taxi_cost` 时才用）
  - `--rail-min-transfer`、`--rail-via`、`--rail-arg`：透传给 rail-transfer

## 数据来源与可信度（必须如实转述）

| 方式 | 数据来源 | 可信度 |
|---|---|---|
| 铁路 | rail-transfer skill 查 12306 | **实测**（余票/票价动态变化） |
| 公共交通 | 高德「公交路径规划」`v3/direction/transit/integrated`（含跨城，跨城方案里会带火车车次时刻） | **实测**（线路/换乘/票价/步行段都是高德返回） |
| 步行 | 高德「步行路径规划」`v3/direction/walking` | 实测；无 Key 时按 4.8 km/h 估算 |
| 打车 | 距离时长=高德驾车路线；车费=高德 `taxi_cost`（按出租车计价估的） | **估价**，不是网约车实时报价 |
| 自驾 | 高德驾车路线（里程/时长/过路费实测）+ 油电假设 | 路线实测，成本含假设 |
| 接驳 | 高德公交规划 / 打车，按 `--access-mode` 取；没 Key 才退回假设值 | 优先实测 |

打车：高德只给「出租车计价」的估价，**滴滴/高德的网约车实时价没有公开接口**，
有浮动和优惠，不要说成实价。

排序规则（`--prefer`）：`time` 只看总耗时、`price` 只看总费用、`balance` 用
「时间排名 + 费用排名」，排名和相同时优先**更快**的那条。门到门总时长
= 接驳 + 铁路/乘车 + 接驳，**不含候车时间**（不知道你几点出门；要算等车请另行说明）。

## 没有高德 Key 怎么办

1. 打开 <https://console.amap.com/dev/key/app>，登录/注册高德开放平台开发者。
2. 「应用管理 → 我的应用 → 创建新应用」（名字随意）。
3. 在新应用里「添加 Key」，**服务平台必须选「Web服务」**（选成 JS/Android 会报 10009）。
4. 复制 32 位 Key，任选一种填法：
   - `setx AMAP_KEY "<key>"`（之后新开的进程都能读到）
   - 或写进 `references/amap.json`：`{"key": "<key>"}`（本机私用，别提交到任何仓库）
   - 或每次查询加 `--amap-key <key>`
5. 用 `python scripts/trip_planner.py --detect` 验证：应显示 `✅ 有效（来源 …）`。

个人开发者有每日免费额度（路径规划类按次计），具体额度以控制台「配额」页为准。
没 Key 时脚本不会报错：公交/打车/步行退回估算，并在 `warnings` 里写明。

## 执行步骤

1. 运行 `python scripts/trip_planner.py --detect`：确认 rail-transfer 与高德 Key 都就绪。
2. 问清 A/B（站名 + 坐标/城市）；城市多站时脚本返回 `blocked` + 候选站，先让主人选。
3. **问接驳假设**：默认 `--access auto --access-mode fastest` 用高德算（公交/打车取更快）；
   如果主人只想坐公交就 `--access-mode transit`；能接受假设值就 `--access assume`。
4. 问偏好：最早到 / 最便宜 / 综合；默认 `balance`。
5. 运行脚本，读报告或 `--format json`。
6. **转述时要带上**：推荐项、对比表、公共交通的每一段（含步行距离与时长）、
   接驳用了公交还是打车（以及另一条要多久）、假设与可信度、`⚠` 告警。
   步行段不要吞掉——「这趟要走 1.9 km」是主人真正在意的信息。

## 失败分流

- 找不到 rail-transfer 脚本 → `environment-missing`：先修 rail-transfer（`--detect`）。
- 铁路站点歧义 → `blocked`（退出码 2）：**不打推荐**，把「需要确认」里的候选站列给主人。
- 没有高德 Key / Key 无效（`INVALID_USER_KEY`）→ 公交/打车/步行降级估算并告警，**必须说明**。
- Key 配额用完（`DAILY_QUERY_OVER_LIMIT`）→ 今天改用估算或换 Key。
- 平台类型不对（`10009`）→ 提示主人在控制台把 Key 的服务平台改成「Web服务」。
- 跨城没有公交数据 → 公共交通不出现，说明「高德没有跨城公交方案」，铁路/自驾照常。
- 接驳站点地理编码失败 → 告警并退回假设值（`--access assume`），别当成真实数据。
- 12306 限流/超时 → `connection-failure`：稍后重试或加 `--rail-via`。
- 铁路空结果 → 脚本会自动加 `--no-cache` 重查一次（余票缓存可能留着「没车」的旧结果）；
  仍空再考虑 `--rail-arg=--no-cache` / `--rail-via`。

## 平台说明

- 铁路查询依赖 Edge/Chrome + `websocket-client`（rail-transfer 负责），本机需能直连 12306。
- 高德接口走 `https://restapi.amap.com/v3/...`，只需 `urllib`，无第三方依赖。
- Key 查找顺序：`--amap-key` > 环境变量 `AMAP_KEY` > `references/amap.json`
  （`amap.json` 已在 `.gitignore`，别提交；模板见 `references/amap.example.json`）。
- 地理编码只用来把**站名**换成站点坐标（接驳用），不会拿站名/城市名去冒充 A/B 的坐标。
- 计价/估速/步行参数在 `references/costs.json`；有 Key 时大部分被高德实测值替代。
- 改完代码先跑：`python -m pytest -q`（54 个离线单测）+ `python scripts/selftest.py`。
- 脚本 UTF-8 输出，Windows 控制台已做编码兜底；`--version` 看版本。

## 输出约定

- 人读报告：方式对比表（总耗时 / 总费用 / 可信度 / 推荐标记）+ 逐项明细。公共交通会**逐段列出**：
  步行 1291 米（约 20 分）到 2号口 进站 → 地铁S6号线 崇明→马群（10 站，43 分）→ … →
  地铁6号线 明故宫→南京南站（6 站，18 分），8号口 出站。
- `--format json`：`status` / `summary` / `evidence` / `next_action` / `failure_category` / `data`；
  `data.comparison` 每项含 `mode` / `name` / `total_min` / `cost` / `confidence` / `detail` /
  `note` / `recommended`；公共交通条目额外带 `legs`（逐段 `kind`/`text`/`steps`/`from`/`to`）；
  `data.transit.walk_m` 是总步行米数、`data.transit.walk_legs` 只含步行段；
  铁路在 `data.rail`，接驳在 `data.access`（每端含 `source`：高德打车/高德公交/高德步行/假设值）。
- `blocked` 时 `data.rail.needs_confirmation` 是候选站清单，所有 `recommended` 为 `false`。
- 退出码：`success` 0，`blocked` 2，`failure` 1。

## 交接关系

- 只做**查询与比价**，不代下单、不代叫车；铁路购票去 12306，打车去对应 App。
- 铁路细节（换乘、缓冲、票）以 rail-transfer 的输出为准。
