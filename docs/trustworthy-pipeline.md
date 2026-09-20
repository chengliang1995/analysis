# 可信选股与复盘流水线

本文说明如何避免「实盘 / 模拟 / 回测 / AI 调参」各走一路、问题反复出现。

## 1. 单一选股管道

| 场景 | 应使用的入口 |
|------|----------------|
| 实盘早盘超短 | `UltraShortScanner` + `build_selection_tuning(for_sim=False)` |
| 模拟超短买入 | 同上 + `_sim_ultra_quality_ok`、扫描价/缺口过滤 |
| 模拟历史回放选股 | `SimReplayEngine._backtest_select_for_day` → `_analyze_single(hist_df=截至当日)` + 与模拟相同的 tuning / 质量门 |
| 中线模拟买入 | **优先** `get_breakout_day_recommendations`（突破日）；无标的时才降级 `get_buy_signal_recommendations(today_only=True)` |

**禁止**：回测用简化 MA 交叉而实盘用 scanner；模拟用历史信号价而规则要求当日价。

### 中线策略优先级（2026-09 跟进证据）

| 优先级 | 策略 | 样本口径 | 胜率 / 均益 | 处置 |
|--------|------|----------|-------------|------|
| 主 | 三倍量+一阳穿三线 **突破日** | tracker 满期 10 日≥+3% | ~63% / +5.8%（n=27） | `get_breakout_day_recommendations` |
| 次 | 观察池缩量站稳 MA5 | watchlist 结算>+5% | ~15% / −2.0%（n=109） | 仅无突破日候选时降级 |
| 慎用 | MA20 突破+MA5 回踩 | tracker n=8 | 25% / −4.2% | 强制缩量 + 门槛≥68 |
| 停用倾向 | 纯底背离 | tracker n=13 | 30.8% / −2.3% | 不再作为选股主轴 |

费用粗算（佣金万 2.5 双边+印花税万 5）：约 0.10%，突破日均益扣费后仍为正。

### 学习闭环（禁止无证据抬参）

1. `scripts/evaluate_all_strategies.py` → `output/strategy_eval/all_strategies_eval.json`
2. `quantpy.strategy_policy.refresh_policy` → 仅当 n 达标才写约束
3. `build_selection_tuning` 应用政策；`run_tuning_pipeline(mode=full)` 落盘
4. `quantpy.ai_strategy_analyst`：统计归因必跑；LLM 可选且禁止编造数字

```bash
python daily_advisor.py strategy-eval
python daily_advisor.py strategy-ai
python daily_advisor.py review-tune
```

## 2. 调参治理（`selection_tuning.py`）

- 超短自动调参：`SIM_TUNING_MIN_TRADES`（默认 15）笔以下**不改**门槛。
- 分档统计：`SIM_TUNING_MIN_BUCKET`（默认 8）笔以下不因单档胜率拒档。
- 收紧与放宽对称：近 30 笔胜率 ≥52% 且均益 >0.5% 时可**下调** `ultra_min_score`；胜率差/止损率高时**上调**。
- AI 学习：`sim_replay.run_review` 失败时必须 `logger.warning` 且 `review["ai_fallback"]=true`，禁止静默 `except: pass`。

## 3. 数据与成本

- 涨停阈值：创业板/科创板 20%，主板约 10%（`check_limit_up_signal(..., code=)`）。
- 模拟平仓按分项费率扣佣金/印花税/滑点；旧 `round_trip_cost_pct` 在升级时清零，避免双重扣费。
- 持仓天数：模拟、日记与提醒统一为**交易日**（`trade_math.hold_trading_days`）。
- 缺行情：市值按成本计，浮盈不入账（`trade_math.mark_position`），禁止把缺价当成 0 元亏光。

## 4. 发布前自检清单

1. `python -c "from quantpy.sim_replay import SimReplayEngine; from quantpy.ai_learning_optimizer import AILearningOptimizer"`
2. 改 scanner / tuning 后：跑 `replay_backtest(days=5)` 与一次 `run_daily(force_select=False)` 对比是否同门槛。
3. 改 watchlist 规则后：加载 summary 确认 `_cleanup_buy_prompts` 无大量残留 `buy_signal`。
4. 新参数进 `SimConfig` 时确认 `_load_state` 合并默认值，旧 `sim_state.json` 可升级。

## 5. 定时任务

- 相位以 `scripts/phases.json` 为准；模拟复盘由 `run_daily` 内 `review_interval` 触发，不依赖单独 AI 相位。
- 日志目录 `logs/` 保留 ≥14 天（`paths.RETENTION_DAYS`）。
