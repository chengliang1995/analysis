# 示例

## 用户：「提交一下」

1. 跑 status / diff / log
2. 发现改了 `quantpy/sim_midterm.py` 与面板模板
3. Commit message：

```
update sim_midterm MA20 account rules and web panel wiring

Align midterm sim with fish-body exits so UI matches strategy.
```

4. 提交成功后 prepend 到 `docs/git-commit-trace.md`：

```markdown
## 2026-09-18 18:20 | a1b2c3d

- **message**: update sim_midterm MA20 account rules and web panel wiring
- **why**: 模拟盘出场与 UI 与 MA20 鱼身策略对齐，便于对照实盘
- **files**:
  - `quantpy/sim_midterm.py` — MA20 账户与出场
  - `templates/partials/panels_sim.html` — 面板文案
- **stats**: 2 files changed, +40/-12
- **chat**: 中线改 MA20 鱼身 + 20 万模拟

---
```

5. 回复用户：`a1b2c3d` 已提交，追溯已写入 `docs/git-commit-trace.md`。

## 用户：「最近提交追溯一下」

打开 `docs/git-commit-trace.md`，列出最近 3–5 条的 hash / message / why；若文件空或过旧，用 `git log -10 --oneline` 补充说明。
