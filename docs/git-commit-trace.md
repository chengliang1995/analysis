# Git 提交追溯

由 skill `git-self-commit-trace` 在每次成功 commit 后 **prepend** 追加。最新在上。

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
