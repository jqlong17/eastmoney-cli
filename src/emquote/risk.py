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


def infer_limit_pct(symbol: str | None, name: str | None = None) -> dict[str, Any]:
    """粗估涨跌停幅度（研究用；未覆盖注册制/特别处理全部细则）。"""
    sym = (symbol or "").upper()
    code = sym.split(".")[0]
    nm = name or ""
    if "ST" in nm.upper() or "st" in nm:
        pct, board = 0.05, "ST"
    elif code.startswith(("300", "301", "688")):
        pct, board = 0.20, "chi_next_star"
    elif code.startswith(("8", "4")) or sym.endswith(".BJ"):
        pct, board = 0.30, "bse"
    else:
        pct, board = 0.10, "main"
    return {"limit_pct": pct, "board": board, "heuristic": True}


def limit_bands(
    ref_close: float,
    *,
    symbol: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    info = infer_limit_pct(symbol, name)
    pct = float(info["limit_pct"])
    up = round_price(ref_close * (1.0 + pct))
    down = round_price(ref_close * (1.0 - pct))
    return {
        **info,
        "ref_close": round_price(ref_close),
        "limit_up": up,
        "limit_down": down,
    }


def check_limit_constraints(
    *,
    buy: float,
    sell: float,
    stop: float,
    ref_close: float,
    symbol: str | None = None,
    name: str | None = None,
    near_pct: float = 0.005,
) -> dict[str, Any]:
    """检查条件单价是否越出/贴近视涨跌停（可能无法成交）。"""
    bands = limit_bands(ref_close, symbol=symbol, name=name)
    up = float(bands["limit_up"])
    down = float(bands["limit_down"])
    near = max(ref_close * near_pct, 0.01)
    flags: list[str] = []
    if buy >= up - near:
        flags.append("买入触发接近/高于涨停，可能无法买入成交")
    if buy <= down + near:
        flags.append("买入触发接近/低于跌停，流动性与成交不确定")
    if sell >= up - near:
        flags.append("止盈接近/高于涨停，上涨途中可能封板难卖在目标价")
    if stop <= down + near:
        flags.append("止损接近/低于跌停，下跌时可能无法按止损价卖出")
    if stop >= buy:
        flags.append("止损不低于买入价，计划结构无效")
    if sell <= buy:
        flags.append("止盈不高于买入价，计划结构无效")
    return {
        **bands,
        "flags": flags,
        "ok": len(flags) == 0,
        "note": "仅按昨收/参考价×板幅度估算；未模拟盘中换日涨跌停与停牌",
    }


def position_size(
    *,
    capital: float,
    risk_pct: float,
    buy: float,
    stop: float,
    lot_size: int = 100,
) -> dict[str, Any]:
    """按「单笔最多亏净值 risk_pct」估算股数（A 股 100 股整手）。"""
    capital = float(capital)
    risk_pct = float(risk_pct)
    risk_per_share = max(float(buy) - float(stop), 1e-9)
    risk_budget = capital * risk_pct
    raw_shares = risk_budget / risk_per_share
    shares = int(raw_shares // lot_size) * lot_size
    notional = shares * float(buy)
    max_loss = shares * risk_per_share
    return {
        "capital": capital,
        "risk_pct": risk_pct,
        "risk_budget": round(risk_budget, 2),
        "risk_per_share": round_price(risk_per_share),
        "shares": shares,
        "lots": shares // lot_size if lot_size else 0,
        "notional": round(notional, 2),
        "max_loss_if_stop": round(max_loss, 2),
        "lot_size": lot_size,
        "feasible": shares >= lot_size,
        "note": (
            "研究用仓位：未含费用/滑点/最小成交约束；"
            "若 shares=0 说明风险预算买不起 1 手或止损过近"
        ),
        "heuristic": True,
    }
