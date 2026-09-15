"""风险与成本辅助（启发式，研究用）。

- ATR：波动自适应止损间距
- 费用：佣金/印花税/滑点的简化往返假设
"""

from __future__ import annotations

from typing import Any

from .levels import round_price


def true_ranges(bars: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    prev_close: float | None = None
    for b in bars:
        h = float(b["high"])
        low = float(b["low"])
        c = float(b["close"])
        if prev_close is None:
            tr = max(h - low, 0.0)
        else:
            tr = max(h - low, abs(h - prev_close), abs(low - prev_close))
        out.append(tr)
        prev_close = c
    return out


def atr(bars: list[dict[str, Any]], window: int = 14) -> float:
    """简易 ATR（SMA of True Range）。"""
    if not bars:
        return 0.0
    trs = true_ranges(bars)
    w = max(1, min(window, len(trs)))
    return sum(trs[-w:]) / w


def min_stop_gap(
    last_close: float,
    bars: list[dict[str, Any]],
    *,
    atr_mult: float = 0.8,
    floor_pct: float = 0.005,
    absolute_floor: float = 0.05,
) -> dict[str, Any]:
    """止损与买入区间最小间距：max(ATR×倍数, 价格×floor%, absolute_floor)。"""
    atr_v = atr(bars, window=14)
    by_atr = round_price(atr_v * atr_mult)
    by_pct = round_price(float(last_close) * floor_pct)
    gap = max(by_atr, by_pct, absolute_floor)
    return {
        "gap": gap,
        "atr": round(atr_v, 4),
        "atr_mult": atr_mult,
        "floor_pct": floor_pct,
        "method": "max(ATR×mult, price×floor%, absolute_floor)",
        "heuristic": True,
    }


def round_trip_cost_pct(
    *,
    commission_rate: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage_one_way: float = 0.0005,
) -> float:
    """A 股简化往返成本率：买佣+卖佣+印花税+双边滑点。"""
    return 2.0 * commission_rate + stamp_duty + 2.0 * slippage_one_way


def net_risk_reward(
    buy: float,
    sell: float,
    stop: float,
    *,
    cost_pct: float | None = None,
) -> dict[str, Any]:
    """几何 RR + 扣简化费用后的净 RR（研究用）。"""
    if cost_pct is None:
        cost_pct = round_trip_cost_pct()
    risk = buy - stop
    reward = sell - buy
    gross_rr = round(reward / risk, 4) if risk > 0 else None

    # 买入多付、卖出少收；止损卖出也少收 → 风险略放大
    buy_eff = buy * (1.0 + cost_pct / 2.0)
    sell_eff = sell * (1.0 - cost_pct / 2.0)
    stop_eff = stop * (1.0 - cost_pct / 2.0)
    net_risk = buy_eff - stop_eff
    net_reward = sell_eff - buy_eff
    net_rr = round(net_reward / net_risk, 4) if net_risk > 0 else None

    return {
        "risk_per_share": round_price(risk),
        "reward_per_share": round_price(reward),
        "ratio_gross": gross_rr,
        "ratio_net": net_rr,
        "cost_pct_round_trip": round(cost_pct, 6),
        "cost_note": "简化假设：佣金+印花税+滑点；未含冲击成本/涨跌停无法成交",
        "heuristic": True,
    }
