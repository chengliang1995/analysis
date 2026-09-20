---
name: serenity-stock-choke
description: >-
  A股卡脖子/供应链瓶颈选股（Serenity 框架）：沿产业链向上游追溯瓶颈节点，
  筛真瓶颈小盘股并输出六步结构化报告。Use when user mentions Serenity、卡脖子、
  供应链瓶颈、霍尔木兹、国产替代节点、分析XX板块卡脖子，或 runs
  `python daily_advisor.py serenity --theme ...`.
  Do NOT use for pure MA/情绪超短或中线 MA20 买卖点（那是 stock-analysis-master）。
---

# Serenity A股卡脖子框架（本仓库适配版）

> 沿着产业链向上游追溯，找到「一旦断货，万亿产业就要地震」的关键节点——该节点上有壁垒的小盘股，才是弹性来源。

这是**供应链瓶颈分析**，不是均线交易系统。与 `stock-analysis-master`（三维：均线/消息/情绪）**并存、不互相替换**。

## 双模板路由（必读）

| 用户意图 | 启用 skill | 数据桶 | CLI |
|---------|-----------|--------|-----|
| 卡脖子 / Serenity / 产业链瓶颈 / 国产替代节点 | **本 skill** | `data/serenity_choke_picks.json` · `output/serenity/` | `serenity` / `serenity-track` |
| 均线结构 / 买卖点 / 超短·中线复盘 / 胜率归因 | `stock-analysis-master` | `midterm_pick_tracker` · 超短/中线产物 | `midterm` / `scan` / `review` |
| 热门概念强弱成份股（非卡脖子叙事） | 旧板块模块 | `output/sector/` | `sector`（Web） |

**兼容规则**

1. **禁止**把 Serenity 候选写入 `midterm_pick_tracker` 或覆盖 `sector_latest.json`。
2. Serenity 先出「瓶颈标的池」；若用户要买卖点，再**二次**调用 `stock-analysis-master` 做 MA20/情绪过滤（标注「二次过滤」）。
3. 旧 `sector` 推荐按涨跌/换手热度打分；本框架按「真瓶颈 + 壁垒 + 弹性」筛选——结论标签必须带 `strategy=serenity_choke`。

## 何时启用

- 「用 Serenity 分析 XX」「XX 板块卡脖子」「找 XX 供应链瓶颈」
- `python daily_advisor.py serenity --theme 光模块`
- 不适用：纯题材无产业逻辑、只问大盘点位、只要精确 PE 估值

## 六步推理（强制顺序）

1. **周期**：需求爆发 / 技术跃迁 / 供给受限（三选一 + 依据）
2. **溯源**：终端→组装→零部件→材料→矿产；标出卡脖子层
3. **标的**：瓶颈节点上的 A 股；填四维信号卡
4. **真伪**：六条排除规则（见 [reference.md](reference.md)）
5. **多空**：做多 3–5 条 vs 风险 2–3 条（禁止单边多头）
6. **报告**：固定七段（模板见 [templates.md](templates.md)）

## 本仓库数据入口（替代 neodata/westock）

优先用现有模块，缺证据标「待验证」，禁止编造：

| 需求 | 入口 |
|------|------|
| 跑筛与落盘 | `quantpy/serenity_choke_advisor.py` · `python daily_advisor.py serenity --theme ...` |
| **模拟盘 20 万** | `quantpy/sim_serenity.py` · `python daily_advisor.py sim-serenity --theme ...` · 仪表盘「卡脖子模拟选股」 |
| 板块列表/成份 | `fetch_board_list` / `fetch_board_constituents`（`stock_data`） |
| 行情/市值/换手 | `get_market_spot` |
| K 线与均线结构 | `get_stock_hist`（默认 `patch_live=False`） |
| 跟进胜率（独立） | `data/serenity_choke_picks.json` · `serenity-track` |
| 旧热门板块（对照，勿混） | `sector_recommender` · `output/sector/` |

Agent 流程：先跑 CLI/模块拿候选与资金面事实 → 再按六步补全叙事与排除理由 → 输出七段报告。

## 行为约束

- 不构成投资建议；用「倾向/观察/风险」
- 小盘弹性大、波动大；逻辑验证可能 1–3 年
- 不改中线/超短选股参数，除非用户明确要求
- 细节、排除表、四维卡字段见 [reference.md](reference.md)；报告骨架见 [templates.md](templates.md)
