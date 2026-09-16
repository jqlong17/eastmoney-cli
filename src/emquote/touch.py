"""共享触达模拟：plan 与 calibrate 用同一套轨位与触价规则。

事件语义与东财条件单对齐：
1. 用当时末段 bars 估通道 → 生成 buy/TP/SL（及买入区）。
2. 有效期内用 K 线 high/low 与价格带相交判定触达。
3. 同根既触止盈又触止损 → 保守记 stop_first。
"""

from __future__ import annotations

from typing import Any

from .levels import compute_channel, round_price
from .risk import min_stop_gap


OUTCOMES = (
    "tp_first",
    "stop_first",
    "expire_unfilled",
    "timeout_after_fill",
)


def build_rails(
    bars_slice: list[dict[str, Any]],
    *,
    kind: str,
    channel_width: float = 2.0,
    interval: str = "5m",
) -> dict[str, float]:
    """由一段 bars 生成条件单轨位（与校准回放同源）。"""
    if not bars_slice:
        raise ValueError("bars_slice 为空，无法生成轨位")
    packed = compute_channel(
        bars_slice,
        kind=kind,
        window=0,
        width=channel_width,
        interval=interval,
        full_range=False,
        causal=True,
    )
    lower = float(packed["last_lower"])
    mid = float(packed["last_mid"])
    upper = float(packed["last_upper"])
    last_close = float(bars_slice[-1]["close"])
    pad = max(round_price(last_close * 0.002), 0.01)
    gap_info = min_stop_gap(last_close, bars_slice)
    gap = float(gap_info["gap"])

    if kind == "donchian":
        buy = round_price(upper + pad)
        half = max(abs(upper - mid), gap)
        sell = round_price(buy + half)
        stop = round_price(mid)
        buy_low, buy_high = buy, round_price(buy * 1.005)
        style = "突破确认（多头）"
    else:
        buy = round_price(lower)
        sell = round_price(upper)
        stop = round_price(lower - pad)
        band = max(round_price(abs(mid - buy) * 0.25), 0.02)
        buy_low = round_price(max(stop + 0.01, buy - band))
        buy_high = round_price(buy + band)
        if buy_low - stop < gap:
            stop = round_price(buy_low - gap)
        style = "回踩买入区"

    if buy - stop <= 0:
        stop = round_price(buy - gap)
    if sell <= buy:
        sell = round_price(buy + max(gap, abs(mid - lower), 0.05))

    return {
        "buy": float(buy),
        "sell": float(sell),
        "stop": float(stop),
        "buy_low": float(buy_low),
        "buy_high": float(buy_high),
        "mid": float(mid),
        "lower": lower,
        "upper": upper,
        "gap": gap,
        "style": style,  # type: ignore[dict-item]
        "kind": kind,  # type: ignore[dict-item]
    }


def simulate_touch(
    bars: list[dict[str, Any]],
    t: int,
    rails: dict[str, float],
    *,
    horizon: int,
) -> str:
    """从决策点 t 之后模拟触达结局。

    返回: tp_first | stop_first | expire_unfilled | timeout_after_fill
    """
    n = len(bars)
    end = min(n - 1, t + horizon)
    buy_low = float(rails["buy_low"])
    buy_high = float(rails["buy_high"])
    sell = float(rails["sell"])
    stop = float(rails["stop"])

    filled_at: int | None = None
    for i in range(t + 1, end + 1):
        lo = float(bars[i]["low"])
        hi = float(bars[i]["high"])
        if lo <= buy_high and hi >= buy_low:
            filled_at = i
            break
    if filled_at is None:
        return "expire_unfilled"

    for j in range(filled_at + 1, end + 1):
        lo = float(bars[j]["low"])
        hi = float(bars[j]["high"])
        hit_stop = lo <= stop
        hit_tp = hi >= sell
        if hit_stop and hit_tp:
            return "stop_first"
        if hit_stop:
            return "stop_first"
        if hit_tp:
            return "tp_first"
    return "timeout_after_fill"


def recent_decision_preview(
    bars: list[dict[str, Any]],
    *,
    kind: str,
    channel_width: float,
    interval: str,
    lookback: int,
    horizon: int,
) -> dict[str, Any]:
    """用共享引擎回放「最近一个合格决策点」的触达结局（与 calibrate 同尺）。"""
    w = min(max(int(lookback), 20), len(bars))
    h = max(int(horizon), 1)
    last_t = len(bars) - h - 1
    if last_t < w - 1:
        return {
            "ok": False,
            "reason": "样本不足以回放最近决策点",
            "engine": "shared_touch",
        }
    t = last_t
    slice_bars = bars[t - w + 1 : t + 1]
    try:
        rails = build_rails(
            slice_bars,
            kind=kind,
            channel_width=channel_width,
            interval=interval,
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc), "engine": "shared_touch"}
    if float(rails["buy"]) <= float(rails["stop"]) or float(rails["sell"]) <= float(rails["buy"]):
        return {"ok": False, "reason": "轨位不合法", "engine": "shared_touch"}
    outcome = simulate_touch(bars, t, rails, horizon=h)
    return {
        "ok": True,
        "engine": "shared_touch",
        "decision_index": t,
        "lookback_bars": w,
        "horizon_bars": h,
        "rails": {k: rails[k] for k in ("buy", "sell", "stop", "buy_low", "buy_high")},
        "outcome": outcome,
        "note": "最近合格决策点的回放结局；与 calibrate 共用 simulate_touch，不是未来预测。",
    }
