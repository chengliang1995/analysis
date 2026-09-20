# 短线强势股 · 参考手册

## 与超短的分工

| | short-term-stock-picker | ultra_short_scanner |
|---|-------------------------|---------------------|
| 时间窗 | 近 20 日涨停基因 | 当日快照 |
| 市值 | ≤150 亿硬顶 | 无统一顶 |
| 换手 | 0.5–10%（控情绪） | 高换手可加分 |
| 均线 | MA5>MA10>MA20 硬门槛 | MA5 站稳为主 |
| strategy_id | `short_term_picker` | `ultra_short` |

## 涨停判定

使用 `StrategyOptimizer.limit_up_pct_threshold(code)`：

- 300/301/688/689 → 19.5%
- 其余 → 9.8%

K 线路径：`pct_chg >= threshold - 0.3` 计一次涨停。

## 技术分细则（与原版对齐）

- 通过均线多头硬筛后基础分 ≈ 30
- 量比 ≥1.5 → +15；≥1.2 → +10；否则 +5
- 近 3 日收涨且量比≥1.2 → +10
- 换手 2–8% → +5

量比 = 近 5 日均量 / 前 5 日均量。

## 落盘

- `output/short_term/short_term_YYYYMMDD_HHMMSS.json`
- `output/short_term/short_term_latest.json` / `.md` / `.csv`
