"""Evidence-bound strategy AI analysis.

Statistical attribution always runs. LLM (optional) may only rephrase/cite
numbers present in the evidence payload — never invent win rates or returns.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from quantpy.paths import OUTPUT_DIR
from quantpy.strategy_policy import (
    EVAL_DIR,
    POLICY_FILE,
    StrategyPolicy,
    build_policy_from_eval,
    load_policy,
    refresh_policy,
)

logger = logging.getLogger(__name__)

AI_ANALYSIS_DIR = OUTPUT_DIR / "ai_analysis"


def build_statistical_attribution(
    eval_report: Optional[dict] = None,
    policy: Optional[StrategyPolicy] = None,
) -> dict:
    """Pure statistical summary — no LLM."""
    path = EVAL_DIR / "all_strategies_eval.json"
    if eval_report is None and path.exists():
        try:
            eval_report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            eval_report = {}
    eval_report = eval_report or {}
    policy = policy or load_policy()

    ranked = []
    for s in eval_report.get("strategies") or []:
        n = int(s.get("n") or 0)
        if n < 1:
            continue
        ranked.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "family": s.get("family"),
            "n": n,
            "win_rate": s.get("win_rate"),
            "avg_return": s.get("avg_return"),
            "verdict": s.get("verdict"),
        })
    ranked.sort(
        key=lambda x: (
            0 if (x.get("n") or 0) >= 8 else 1,
            -(x.get("win_rate") or 0),
        )
    )

    primary = [r for r in ranked if r.get("verdict") == "主策略候选"]
    demote = [r for r in ranked if r.get("verdict") in ("不适用/应降级", "偏弱需优化")]

    conclusions = list(policy.actions)
    if not conclusions and primary:
        conclusions.append(
            f"主策略候选：{primary[0]['name']}（n={primary[0]['n']} 胜率{primary[0]['win_rate']}%）"
        )
    conclusions.append("不是投资建议；结论仅对已结算样本成立。")

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "engine": "statistical",
        "fee_drag_pct": eval_report.get("fee_drag_pct"),
        "policy": policy.to_dict(),
        "ranked": ranked[:20],
        "primary_candidates": primary,
        "demote_list": demote[:10],
        "conclusions": conclusions,
        "not_advice": True,
    }


def _llm_attribution(evidence: dict) -> Optional[List[str]]:
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY")):
        return None
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY", "")
    base_url = os.environ.get("AI_API_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AI_MODEL", "gpt-4o-mini")

    prompt = (
        "你是 A 股量化复盘助手。下面 JSON 是唯一允许引用的证据。"
        "任务：用中文写 4-6 条归因与行动建议。"
        "硬性规则：\n"
        "1) 每条建议必须点名证据里出现过的策略名与数字（n/胜率/均益），禁止编造。\n"
        "2) 样本 n<8 只能写「样本不足」，不得下强弱结论。\n"
        "3) 结尾必须有一句「不是投资建议」。\n"
        "4) 只输出 JSON 数组，元素为字符串。\n\n"
        f"证据：{json.dumps(evidence, ensure_ascii=False)}"
    )
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "只输出 JSON 字符串数组。不得添加证据中不存在的胜率或收益数字。",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=45,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # strip markdown fences if any
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        extra = json.loads(text)
        if isinstance(extra, list):
            return [str(x) for x in extra if str(x).strip()]
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
        logger.warning("策略 AI 归因 LLM 失败，回退统计引擎: %s", exc)
    return None


def run_strategy_ai_analysis(
    *,
    refresh_eval: bool = False,
    use_llm: bool = True,
    show_progress: bool = False,
) -> dict:
    """Closed-loop: (optional) refresh policy/eval → statistical attribution → optional LLM."""
    if refresh_eval:
        policy = refresh_policy(run_eval=True)
    else:
        policy = load_policy()
        if not policy.actions and not POLICY_FILE.exists():
            policy = refresh_policy(run_eval=True)

    base = build_statistical_attribution(policy=policy)
    llm_lines = _llm_attribution(base) if use_llm else None
    if llm_lines:
        base["engine"] = "statistical+llm"
        # Keep statistical conclusions first; append LLM only if non-duplicate
        merged = list(base["conclusions"])
        for line in llm_lines:
            if line not in merged:
                merged.append(line)
        base["conclusions"] = merged[:12]
        base["llm_suggestions"] = llm_lines

    AI_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out = AI_ANALYSIS_DIR / f"strategy_attribution_{stamp}.json"
    out.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    md = AI_ANALYSIS_DIR / f"strategy_attribution_{stamp}.md"
    lines = [
        f"# 策略归因分析\n\n",
        f"引擎: {base['engine']} · {base['generated_at']}\n\n",
        "**不是投资建议。**\n\n",
        "## 结论\n",
    ]
    for i, c in enumerate(base.get("conclusions") or [], 1):
        lines.append(f"{i}. {c}\n")
    lines.append("\n## 排名（有样本）\n\n")
    lines.append("| 策略 | n | 胜率% | 均益% | 判定 |\n|---|---|---|---|---|\n")
    for r in base.get("ranked") or []:
        lines.append(
            f"| {r.get('name')} | {r.get('n')} | {r.get('win_rate')} | "
            f"{r.get('avg_return')} | {r.get('verdict')} |\n"
        )
    md.write_text("".join(lines), encoding="utf-8")
    base["report_json"] = str(out)
    base["report_md"] = str(md)

    if show_progress:
        print("=" * 60)
        print(f"策略 AI 归因 · {base['engine']}")
        print("=" * 60)
        for i, c in enumerate(base.get("conclusions") or [], 1):
            print(f"  {i}. {c}")
        print(f"\n已写入 {out}")

    return base
