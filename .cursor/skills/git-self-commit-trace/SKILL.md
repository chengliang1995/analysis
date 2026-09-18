---
name: git-self-commit-trace
description: 自己提交git 并记录提交内容 方便追溯. Creates git commits when the user asks, then appends a structured entry to docs/git-commit-trace.md for later tracing. Use when the user asks to commit, 提交, 自己提交, git commit, or to record/trace commit history.
---

# Git 自提交与追溯

自己提交git 并记录提交内容 方便追溯。

## 何时启用

- 用户明确要求：提交 / commit / 自己提交 / 记一下这次提交
- 用户要求查看或补充提交追溯记录

未明确要求时：**不要**主动 commit。

## 安全红线

- 不改 `git config`
- 不 `push --force`、不 hard reset（除非用户明确要求）
- 不跳过 hooks（`--no-verify` 等）除非用户明确要求
- 不用 `git commit --amend`，除非同时满足：用户要求 amend（或 hook 自动改了需补进刚成功的 commit）、本会话刚创建该 commit、且尚未 push
- 不用 `-i` 交互式 git
- 不提交疑似密钥（`.env`、`credentials.json` 等）；若用户坚持要提交，先警告
- 无变更时不空 commit

## 提交流程

按顺序执行（可并行先跑 status / diff / log）：

1. **并行采集**
   - `git status`
   - `git diff` 与 `git diff --staged`
   - `git log -5 --oneline`（对齐本仓库 message 风格）

2. **分析变更**
   - 区分 staged / unstaged / untracked
   - 草拟 1–2 句 commit message，侧重 **why**，动词准确（add / update / fix）
   - 排除不应入库的文件（密钥、无意义的 `__pycache__` 等，除非用户点名）

3. **提交**
   - `git add` 相关文件
   - 用 HEREDOC 写 message（Windows PowerShell 用 here-string）：

```powershell
git commit -m @"
<message>

"@
```

```bash
git commit -m "$(cat <<'EOF'
<message>

EOF
)"
```

4. **验证**：`git status`；记下完整 hash：`git rev-parse HEAD` 与 `git log -1 --format=%h %s`

5. **记录追溯**（必须）：追加写入 `docs/git-commit-trace.md`（文件不存在则按模板新建）

## 追溯日志格式

路径：`docs/git-commit-trace.md`

每条记录 **prepend**（最新在上），模板：

```markdown
## YYYY-MM-DD HH:MM | <short-hash>

- **message**: <完整 commit message 首行；多行则附正文摘要>
- **why**: <一句话说明为何提交>
- **files**:
  - `path/a` — 简述
  - `path/b` — 简述
- **stats**: <N files changed, +X/-Y>（来自 `git show --stat --oneline -1`）
- **chat**: <可选：会话要点或相关需求一句话>

---
```

规则：
- 只记**本次**成功 commit；失败不写假记录
- `files` 列主要路径，不必贴整份 diff
- 若本次只改了 `docs/git-commit-trace.md` 本身，可跳过二次 commit，除非用户要求把日志一并入库

## 提交后回复用户

简短告知：
1. commit hash + message
2. 已写入 `docs/git-commit-trace.md`
3. 未 push（除非用户同时要求 push）

## 查询追溯

用户问「最近提交了什么 / 追溯」时：读 `docs/git-commit-trace.md` 顶部若干条；不足再用 `git log` 补全，并可选回填缺失条目。

## 详细示例

见 [examples.md](examples.md)。
