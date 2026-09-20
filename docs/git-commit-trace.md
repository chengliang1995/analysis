# Git 提交追溯

由 skill `git-self-commit-trace` 在每次成功 commit 后 **prepend** 追加。最新在上。

---

## 2026-09-20 09:51 | 3447f5c

- **message**: fix: RSI 横盘 NaN 与涨停策略板块阈值对齐
- **why**: 横盘时 avg_loss==0 产生 NaN 导致 RSI 判断静默失效；limit_up_strategy 固定 9.8% 误判创业板/科创板涨停
- **files**:
  - `quantpy/qstock_strategy_optimizer.py` — RSI 零损失分支；`limit_up_pct_threshold` 共用
  - `quantpy/midterm_portfolio_advisor.py` — `_rsi_series` 同步零损失处理
  - `examples/limit_up_strategy_demo.py` — 传入 code
  - `tests/test_core_strategy.py` — 横盘 RSI / 板块阈值回归
- **stats**: 4 files changed, +124/-11
- **chat**: 采纳两处回归修复并提交

---

## 2026-09-20 09:40 | 85859c9

- **message**: fix: 盈亏口径对齐、证据驱动选股政策与学习闭环
- **why**: 缺价/费用/交易日统一口径；按全策略评估把中线主路径改为突破日并收紧弱策略；评估→政策→调参→归因可重跑
- **files**:
  - `quantpy/trade_math.py` — 缺价不入账、费用、交易日
  - `quantpy/strategy_policy.py` / `ai_strategy_analyst.py` — 证据政策与归因
  - `quantpy/sim_midterm.py` / `midterm_triple_volume_selector.py` — 突破日主买点
  - `quantpy/tuning_pipeline.py` / `selection_tuning.py` / `daily_advisor.py` — 闭环与 CLI
  - `scripts/evaluate_all_strategies.py` / `tests/test_*.py` — 评估与校验
  - `.cursor/skills/finance-analysis/` — 金融分析 skill 入库
- **stats**: 28 files changed, +2326/-225
- **chat**: 审视修复 → 策略评估优化 → strategy-eval/ai/review-tune → 提交

---

## 2026-09-18 18:24 | c7562e7

- **message**: 架构优化：patch_live 默认 False，Web action 收敛到 orchestration
- **why**: 回测/复盘不被盘中 bar 污染；Web 大段 elif 收成统一分发，并补调优管线与回归测试
- **files**:
  - `quantpy/stock_data.py` — `get_stock_hist` 默认 `patch_live=False`
  - `quantpy/orchestration.py` — 扩展 action 与 `dispatch_action`
  - `quantpy/web_app.py` — `api_action` 收敛到 orchestration
  - `quantpy/tuning_pipeline.py` — 统一调优管线
  - `tests/test_orchestration_dispatch.py` / `tests/test_tuning_pipeline.py` — 回归
- **stats**: 26 files changed, +1632/-628
- **chat**: 按顺序做 patch_live 默认 False → Web 收敛 orchestration，并提交

---
