"""Evidence-based strategy policy — only act when sample supports it.

Not investment advice. Numbers must come from evaluate_all_strategies /
trackers / sim closed trades. Do not raise thresholds without n and win-rate.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from quantpy.paths import OUTPUT_DIR

logger = logging.getLogger(__name__)

EVAL_DIR = OUTPUT_DIR / "strategy_eval"
POLICY_FILE = EVAL_DIR / "strategy_policy.json"

# Minimum samples before a policy rule may fire
MIN_N_ULTRA = 30
MIN_N_MIDTERM_TREND = 12
MIN_N_WATCHLIST = 40
MIN_N_MA20 = 8


@dataclass
class StrategyPolicy:
    """Actionable policy derived from closed-loop evidence."""

    updated_at: str = ""
    midterm_entry_mode: str = "breakout_day"  # breakout_day | watchlist_shrink
    prefer_breakout_day: bool = True
    demote_watchlist_as_primary: bool = True
    demote_ma20_as_primary: bool = True
    demote_divergence_as_primary: bool = True
    ultra_force_seal_filter: bool = False
    ultra_min_score_floor: Optional[int] = None
    triple_min_score_floor: Optional[int] = None
    ma20_min_score_floor: Optional[int] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    actions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _find_strategy(report: dict, strategy_id: str) -> Optional[dict]:
    for s in report.get("strategies") or []:
        if s.get("id") == strategy_id:
            return s
    return None


def build_policy_from_eval(report: Optional[dict] = None) -> StrategyPolicy:
    """Derive policy from all_strategies_eval.json (or provided report)."""
    if report is None:
        path = EVAL_DIR / "all_strategies_eval.json"
        if path.exists():
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = {}
        else:
            report = {}

    policy = StrategyPolicy(updated_at=datetime.now().isoformat(timespec="seconds"))
    evidence: Dict[str, Any] = {}

    # --- Midterm: breakout vs watchlist vs MA20 vs divergence ---
    breakout = _find_strategy(report, "tracker_triple_breakout")
    watch = _find_strategy(report, "tv_watchlist_shrink")
    ma20_t = _find_strategy(report, "tracker_ma20_pullback")
    ma20_s = _find_strategy(report, "sim_ma20_pullback")
    div = _find_strategy(report, "tracker_divergence")
    ultra = _find_strategy(report, "sim_ultra_short")

    if breakout and (breakout.get("n") or 0) >= MIN_N_MIDTERM_TREND:
        wr = float(breakout.get("win_rate") or 0)
        avg = float(breakout.get("avg_return") or 0)
        evidence["triple_breakout"] = {"n": breakout["n"], "win_rate": wr, "avg": avg}
        if wr >= 55 and avg >= 3:
            policy.prefer_breakout_day = True
            policy.midterm_entry_mode = "breakout_day"
            policy.triple_min_score_floor = 68
            policy.actions.append(
                f"中线主路径=三倍量突破日（n={breakout['n']} 胜率{wr}% 均益{avg}%）"
            )

    if watch and (watch.get("n") or 0) >= MIN_N_WATCHLIST:
        wr = float(watch.get("win_rate") or 0)
        avg = float(watch.get("avg_return") or 0)
        evidence["watchlist_shrink"] = {"n": watch["n"], "win_rate": wr, "avg": avg}
        if wr < 35 or avg < 0:
            policy.demote_watchlist_as_primary = True
            policy.actions.append(
                f"观察池缩量再买不作主路径（n={watch['n']} 胜率{wr}% 均益{avg}%）"
            )

    ma20_n = max(int(ma20_t.get("n") or 0) if ma20_t else 0, int(ma20_s.get("n") or 0) if ma20_s else 0)
    ma20_wr = None
    if ma20_t and (ma20_t.get("n") or 0) >= MIN_N_MA20:
        ma20_wr = float(ma20_t.get("win_rate") or 0)
        evidence["ma20_tracker"] = {
            "n": ma20_t["n"], "win_rate": ma20_wr, "avg": ma20_t.get("avg_return"),
        }
    if ma20_s and (ma20_s.get("n") or 0) >= MIN_N_MA20:
        evidence["ma20_sim"] = {
            "n": ma20_s["n"],
            "win_rate": ma20_s.get("win_rate"),
            "avg": ma20_s.get("avg_return"),
        }
        if ma20_wr is None:
            ma20_wr = float(ma20_s.get("win_rate") or 0)
    if ma20_n >= MIN_N_MA20 and ma20_wr is not None and ma20_wr < 40:
        policy.demote_ma20_as_primary = True
        policy.ma20_min_score_floor = 68
        policy.actions.append(
            f"MA20回踩非主路径（n≥{ma20_n} 胜率{ma20_wr}%），门槛≥68+强制缩量"
        )

    if div and (div.get("n") or 0) >= 8:
        wr = float(div.get("win_rate") or 0)
        evidence["divergence"] = {"n": div["n"], "win_rate": wr, "avg": div.get("avg_return")}
        if wr < 40:
            policy.demote_divergence_as_primary = True
            policy.actions.append(f"底背离停用主轴（n={div['n']} 胜率{wr}%）")

    # --- Ultra short ---
    if ultra and (ultra.get("n") or 0) >= MIN_N_ULTRA:
        wr = float(ultra.get("win_rate") or 0)
        avg = float(ultra.get("avg_return") or 0)
        evidence["sim_ultra"] = {"n": ultra["n"], "win_rate": wr, "avg": avg}
        if wr < 40:
            policy.ultra_force_seal_filter = True
            policy.ultra_min_score_floor = 51
            policy.actions.append(
                f"超短强制封板硬筛+门槛≥51（模拟 n={ultra['n']} 胜率{wr}%）"
            )
        elif wr < 45 and avg < 1:
            policy.ultra_force_seal_filter = True
            policy.ultra_min_score_floor = 48
            policy.actions.append(
                f"超短偏弱：封板硬筛+门槛≥48（n={ultra['n']} 胜率{wr}%）"
            )

    if not policy.actions:
        policy.notes.append("样本不足或证据未触发阈值；保持现有门槛，不空转抬参")
    else:
        policy.notes.append("不是投资建议；政策仅在样本达标时改选股约束")

    policy.evidence = evidence
    return policy


def refresh_policy(*, run_eval: bool = True) -> StrategyPolicy:
    """Optionally re-run evaluation, then build and persist policy."""
    report = None
    if run_eval:
        try:
            from scripts.evaluate_all_strategies import main as eval_main

            report = eval_main()
        except Exception:
            try:
                # fallback: import via path-safe helper
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    "evaluate_all_strategies",
                    Path(__file__).resolve().parent.parent
                    / "scripts"
                    / "evaluate_all_strategies.py",
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    report = mod.main()
            except Exception as exc:
                logger.warning("全策略评估失败，使用已有 JSON: %s", exc)

    policy = build_policy_from_eval(report)
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    POLICY_FILE.write_text(
        json.dumps(policy.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return policy


def load_policy() -> StrategyPolicy:
    if POLICY_FILE.exists():
        try:
            raw = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
            known = {f.name for f in StrategyPolicy.__dataclass_fields__.values()}  # type: ignore
            return StrategyPolicy(**{k: v for k, v in raw.items() if k in known})
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return build_policy_from_eval()


def apply_policy_to_tuning(tuning: Any, policy: Optional[StrategyPolicy] = None) -> List[str]:
    """Mutate SelectionTuning from policy. Returns applied action strings."""
    policy = policy or load_policy()
    applied: List[str] = []

    if "strategy_policy" not in (tuning.sources or []):
        tuning.sources.append("strategy_policy")

    if policy.ultra_force_seal_filter:
        tuning.strict_tag_filter = True
        tuning.require_ultra_tag_any = [
            "强势封板", "封板", "涨停不破开", "连板",
        ]
        tuning.ultra_demote_midhigh_unsealed = True
        tuning.ultra_demote_high_unsealed = True
        applied.append("ultra_force_seal_filter")

    if policy.ultra_min_score_floor is not None:
        old = tuning.ultra_min_score
        tuning.ultra_min_score = max(int(tuning.ultra_min_score), int(policy.ultra_min_score_floor))
        if tuning.ultra_min_score != old:
            applied.append(f"ultra_min_score→{tuning.ultra_min_score}")

    if policy.triple_min_score_floor is not None:
        old = tuning.triple_min_score
        tuning.triple_min_score = max(int(tuning.triple_min_score), int(policy.triple_min_score_floor))
        if tuning.triple_min_score != old:
            applied.append(f"triple_min_score→{tuning.triple_min_score}")

    if policy.ma20_min_score_floor is not None:
        old = tuning.ma20_pullback_min_score
        tuning.ma20_pullback_min_score = max(
            int(tuning.ma20_pullback_min_score), int(policy.ma20_min_score_floor),
        )
        if tuning.ma20_pullback_min_score != old:
            applied.append(f"ma20_min_score→{tuning.ma20_pullback_min_score}")

    # Persist entry preference on tuning object if field exists
    if hasattr(tuning, "midterm_entry_mode"):
        tuning.midterm_entry_mode = policy.midterm_entry_mode
    if hasattr(tuning, "demote_watchlist_as_primary"):
        tuning.demote_watchlist_as_primary = policy.demote_watchlist_as_primary
    if hasattr(tuning, "demote_ma20_as_primary"):
        tuning.demote_ma20_as_primary = policy.demote_ma20_as_primary

    for act in policy.actions:
        if act not in tuning.notes:
            tuning.notes.append(act)
    return applied
