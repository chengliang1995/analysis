---
name: short-term-stock-picker
description: >-
  A股短线强势股筛选：近20日涨停基因 + 流通市值≤150亿 + 均线多头 + 换手0.5–10% + 量比评分。
  Use when user mentions 短线选股、强势股、涨停基因、短线筛选、short-term-stock-picker,
  or runs `python daily_advisor.py short-term`.
  Do NOT use for 当日超短封板扫描(ultra scan)、卡脖子(Serenity)、或中线 MA20 买卖点。
---

# 短线强势股筛选（本仓库适配版）

涨停基因 → 小市值 → 均线多头 → 换手可控 → 量比加分。  
与 `ultra_short_scanner`（当日动量/封板）**并存**：本 skill 偏「近 20 日涨停后仍健康」的次日/波段池。

## 路由

| 意图 | Skill / 模块 | CLI |
|------|--------------|-----|
| 短线强势 / 涨停基因 / ≤150 亿 | **本 skill** | `short-term` |
| 当日超短 / 封板 / 连板 | `stock-analysis-master` + ultra | `scan` |
| 卡脖子 | `serenity-stock-choke` | `serenity` |

禁止写入 `midterm_pick_tracker` / `ultra_short_*.csv`；落盘 `output/short_term/`。

## 硬过滤

1. 流通市值 ≤ **150 亿**
2. 近 **20** 交易日涨停次数 ≥ 1（主板 9.8% / 创业科创 19.5%）
3. 非 ST / 非北交所
4. 价 ≥ MA5 > MA10 > MA20（多头）
5. 换手率近 3 日均 **0.5%–10%**

## 评分

```
综合分 = 涨停次数×20 + 技术分 + 量比×5
技术分：多头基础 + 放量档 + 量价配合 + 换手稳定
```

细节见 [reference.md](reference.md)。

## 本仓库入口

| 需求 | 入口 |
|------|------|
| 扫描落盘 | `quantpy/short_term_picker.py` · `python daily_advisor.py short-term` |
| 行情/市值 | `get_market_spot` |
| K 线 | `get_stock_hist(patch_live=False)` |
| 涨停池（可选加速） | AKShare `stock_zt_pool_em` + 本地 cache；失败则用 K 线回推涨停次数 |
| 仪表盘 | 实盘 →「超短」子页 → 短线强势池 |

## 约束

- 不构成投资建议；缺数据标「待验证」
- 建议交易日收盘后跑，K 线更完整
- 不改超短 `min_score` 等参数，除非用户明确要求
