"""超短买卖参数参考：以扫描时价格计算买点、止损、止盈。"""

from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from quantpy.sim_replay import SimConfig


def compute_ultra_trade_refs(
    scan_price: float,
    *,
    config: Optional["SimConfig"] = None,
    open_price: float = 0,
    pre_close: float = 0,
) -> Dict[str, Any]:
    """根据扫描价生成买卖参数参考。"""
    if config is None:
        from quantpy.sim_replay import SimConfig
        config = SimConfig()

    price = round(float(scan_price or 0), 2)
    if price <= 0:
        return {}

    open_px = round(float(open_price or price), 2)
    zone_low = round(open_px * 0.998, 2)
    zone_high = round(open_px * (1 + config.buy_premium_pct / 100), 2)
    gap_pct = round((open_px - pre_close) / pre_close * 100, 2) if pre_close > 0 else 0.0

    stop = round(price * (1 + config.stop_loss_pct / 100), 2)
    take = round(price * (1 + config.take_profit_pct / 100), 2)

    return {
        "scan_price": price,
        "scan_time_ref": "扫描时",
        "buy_price_ref": price,
        "sell_price_ref": price,
        "open_price_ref": open_px,
        "buy_zone": f"{zone_low}-{zone_high}",
        "buy_zone_low": zone_low,
        "buy_zone_high": zone_high,
        "stop_loss_ref": stop,
        "take_profit_ref": take,
        "gap_pct": gap_pct,
        "stop_loss_pct": config.stop_loss_pct,
        "take_profit_pct": config.take_profit_pct,
        "max_hold_days": config.max_hold_days,
    }


def load_sim_config_for_refs() -> "SimConfig":
    try:
        from quantpy.sim_replay import SimReplayEngine
        return SimReplayEngine().config
    except Exception:
        from quantpy.sim_replay import SimConfig
        return SimConfig()
