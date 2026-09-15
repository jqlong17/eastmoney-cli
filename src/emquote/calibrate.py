"""条件单历史校准（事件研究，walk-forward，研究用）。

事件定义（与东财条件单叙事对齐）：
1. 决策时刻 t：仅用 bars[t-W+1 : t+1] 估计通道轨（无未来段内信息）。
2. 有效期 H 根 K 线内等待买入区触达（用 high/low 触价）。
3. 成交后，在剩余有效期内看先触止盈还是先触止损。
4. 结果：tp_first / stop_first / expire_unfilled / timeout_after_fill。

输出频率 + Beta-Binomial 弱先验收缩后验（经验贝叶斯味道）。
"""

from __future__ import annotations

from typing import Any

from .levels import compute_channel, round_price
from .risk import atr, min_stop_gap, net_risk_reward, round_trip_cost_pct


def _bars_per_day(interval: str) -> int:
    return {
        "1m": 240,
        "5m": 48,
        "15m": 16,
        "30m": 8,
        "60m": 4,
        "1d": 1,
        "day": 1,
    }.get((interval or "5m").lower(), 48)


def _beta_mean(wins: int, n: int, *, a: float = 2.0, b: float = 2.0) -> dict[str, Any]:
    """Beta(a,b) 先验 + Binomial 似然 → 后验均值与 80% 近似区间（正态近似）。"""
    post_a = a + wins
    post_b = b + max(n - wins, 0)
    mean = post_a / (post_a + post_b)
    # 正态近似分位：z≈1.28 → 80%
    var = post_a * post_b / ((post_a + post_b) ** 2 * (post_a + post_b + 1.0))
    std = var ** 0.5
    lo = max(0.0, mean - 1.28 * std)
    hi = min(1.0, mean + 1.28 * std)
    return {
        "mean": round(mean, 4),
        "ci80": [round(lo, 4), round(hi, 4)],
        "prior": {"alpha": a, "beta": b},
        "posterior": {"alpha": round(post_a, 4), "beta": round(post_b, 4)},
        "n": n,
        "wins": wins,
    }


def _rails_for_kind(
    bars_slice: list[dict[str, Any]],
    *,
    kind: str,
    channel_width: float,
    interval: str,
) -> dict[str, float]:
    packed = compute_channel(
        bars_slice,
        kind=kind,
        window=0,
        width=channel_width,
        interval=interval,
        full_range=False,
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
        # 突破后止盈：买入价上方至少半通道或 ATR 间距
        half = max(abs(upper - mid), gap)
        sell = round_price(buy + half)
        stop = round_price(mid)
        buy_low, buy_high = buy, round_price(buy * 1.005)
    else:
        buy = round_price(lower)
        sell = round_price(upper)
        stop = round_price(lower - pad)
        band = max(round_price(abs(mid - buy) * 0.25), 0.02)
        buy_low = round_price(max(stop + 0.01, buy - band))
        buy_high = round_price(buy + band)
        if buy_low - stop < gap:
            stop = round_price(buy_low - gap)

    return {
        "buy": float(buy),
        "sell": float(sell),
        "stop": float(stop),
        "buy_low": float(buy_low),
        "buy_high": float(buy_high),
        "mid": float(mid),
        "lower": lower,
        "upper": upper,
    }


def _simulate_one(
    bars: list[dict[str, Any]],
    t: int,
    rails: dict[str, float],
    *,
    horizon: int,
) -> str:
    """从 t 之后模拟；返回结局标签。"""
    n = len(bars)
    end = min(n - 1, t + horizon)
    buy_low = rails["buy_low"]
    buy_high = rails["buy_high"]
    sell = rails["sell"]
    stop = rails["stop"]

    filled_at: int | None = None
    for i in range(t + 1, end + 1):
        lo = float(bars[i]["low"])
        hi = float(bars[i]["high"])
        # 触价：区间与 K 线价格带相交
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
            # 同根歧义：保守记为止损优先（更不利）
            return "stop_first"
        if hit_stop:
            return "stop_first"
        if hit_tp:
            return "tp_first"
    return "timeout_after_fill"


def calibrate_condition_orders(
    kline: dict[str, Any],
    *,
    kind: str = "vwreg",
    channel_window: int = 96,
    channel_width: float = 2.0,
    validity_days: int = 5,
    step_days: float = 0.5,
    cost_pct: float | None = None,
) -> dict[str, Any]:
    """对单通道风格做 walk-forward 触达校准。"""
    bars = kline.get("bars") or []
    interval = str(kline.get("interval") or "5m")
    bpd = max(1, _bars_per_day(interval))
    w = channel_window if channel_window > 0 else max(bpd, 96 if bpd >= 48 else 20)
    w = min(max(w, 20), len(bars))
    horizon = max(bpd, int(validity_days * bpd))
    step = max(1, int(step_days * bpd))
    if cost_pct is None:
        cost_pct = round_trip_cost_pct()

    if len(bars) < w + horizon + 5:
        return {
            "ok": False,
            "reason": "样本不足以做 walk-forward 校准",
            "bars": len(bars),
            "need_at_least": w + horizon + 5,
            "kind": kind,
        }

    counts = {
        "tp_first": 0,
        "stop_first": 0,
        "expire_unfilled": 0,
        "timeout_after_fill": 0,
    }
    filled_net_rs: list[float] = []
    decisions = 0
    # 决策点：留出未来 horizon，且有足够 lookback
    last_t = len(bars) - horizon - 1
    for t in range(w - 1, last_t + 1, step):
        slice_bars = bars[t - w + 1 : t + 1]
        try:
            rails = _rails_for_kind(
                slice_bars,
                kind=kind,
                channel_width=channel_width,
                interval=interval,
            )
        except ValueError:
            continue
        if rails["buy"] <= rails["stop"] or rails["sell"] <= rails["buy"]:
            continue
        outcome = _simulate_one(bars, t, rails, horizon=horizon)
        counts[outcome] = counts.get(outcome, 0) + 1
        decisions += 1
        if outcome in {"tp_first", "stop_first"}:
            rr = net_risk_reward(rails["buy"], rails["sell"], rails["stop"], cost_pct=cost_pct)
            # 记 +net_rr 或 -1R（费用后）
            if outcome == "tp_first" and rr.get("ratio_net") is not None:
                filled_net_rs.append(float(rr["ratio_net"]))
            else:
                filled_net_rs.append(-1.0)

    filled = counts["tp_first"] + counts["stop_first"] + counts["timeout_after_fill"]
    # 触止盈率：以「已成交」为条件；另报「相对全部决策」
    tp_given_fill = _beta_mean(counts["tp_first"], max(filled, 1))
    # 若几乎无成交，用全部决策里的 tp 作弱信号
    tp_vs_all = _beta_mean(counts["tp_first"], max(decisions, 1))
    stop_given_fill = _beta_mean(counts["stop_first"], max(filled, 1))
    fill_rate = _beta_mean(filled, max(decisions, 1))

    avg_net_r = sum(filled_net_rs) / len(filled_net_rs) if filled_net_rs else None

    # 可读提示（避免过声称）
    if decisions < 8:
        hint = "同窗决策点过少，后验很宽，仅供参考，不可当作胜率承诺。"
        quality = "low_sample"
    elif fill_rate["mean"] < 0.15:
        hint = "历史同类挂单成交偏少；有效期或买入区可能过窄/过远。"
        quality = "low_fill"
    elif tp_given_fill["mean"] + 0.05 < stop_given_fill["mean"]:
        hint = "费用后口径下，历史更常先触止损；回踩单宜降杠杆或等更窄宽度/动能回落。"
        quality = "stop_heavy"
    elif tp_given_fill["mean"] > stop_given_fill["mean"] + 0.05:
        hint = "历史样本中先触止盈略占优（弱先验收缩后）；仍须独立风控。"
        quality = "tp_lean"
    else:
        hint = "先触止盈/止损接近；边缘取决于费用、滑点与日线方向过滤。"
        quality = "mixed"

    return {
        "ok": True,
        "kind": kind,
        "heuristic": False,
        "method": "walk_forward_touch",
        "definition": {
            "lookback_bars": w,
            "horizon_bars": horizon,
            "validity_days": validity_days,
            "step_bars": step,
            "touch_rule": "K线 high/low 与买入区/止盈/止损价相交即视为触达",
            "ambiguity": "同一根既触止盈又触止损 → 记 stop_first（保守）",
            "no_lookahead": "决策只用当时末 W 根估计通道",
        },
        "cost_pct_round_trip": cost_pct,
        "decisions": decisions,
        "counts": counts,
        "rates": {
            "fill": fill_rate,
            "tp_given_fill": tp_given_fill,
            "stop_given_fill": stop_given_fill,
            "tp_vs_all_decisions": tp_vs_all,
            "expire_unfilled_share": round(counts["expire_unfilled"] / max(decisions, 1), 4),
        },
        "avg_net_r_when_resolved": None if avg_net_r is None else round(avg_net_r, 4),
        "quality": quality,
        "hint": hint,
        "disclaimer": (
            "单票、短样本、弱先验收缩的研究回放，不是未来胜率；"
            "未覆盖涨跌停无法成交、停牌与冲击成本。"
        ),
    }


def calibrate_multi(
    kline: dict[str, Any],
    *,
    kinds: list[str] | None = None,
    channel_window: int = 96,
    channel_width: float = 2.0,
    validity_days: int = 5,
) -> dict[str, Any]:
    kinds = kinds or ["vwreg", "donchian"]
    by_kind = {
        k: calibrate_condition_orders(
            kline,
            kind=k,
            channel_window=channel_window,
            channel_width=channel_width,
            validity_days=validity_days,
        )
        for k in kinds
        if k != "none"
    }

    def score(item: dict[str, Any]) -> float:
        if not item.get("ok"):
            return -1.0
        rates = item.get("rates") or {}
        tp = (rates.get("tp_given_fill") or {}).get("mean")
        st = (rates.get("stop_given_fill") or {}).get("mean")
        if tp is None or st is None:
            return -1.0
        # 样本惩罚
        n = float(item.get("decisions") or 0)
        return float(tp) - float(st) + min(n, 30) / 300.0

    best = None
    best_s = -1e9
    for k, item in by_kind.items():
        s = score(item)
        if s > best_s:
            best_s = s
            best = k

    return {
        "symbol": kline.get("symbol"),
        "name": kline.get("name"),
        "interval": kline.get("interval"),
        "by_kind": by_kind,
        "suggested_primary": best,
        "note": "suggested_primary 仅按本段回放的（触止盈−触止损）差选择，样本不足时不可靠。",
    }


def pick_primary_by_regime(
    suggestions: list[dict[str, Any]],
    *,
    width_rank: str | None,
    energy_rank: str | None,
    calibration: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """结合状态 + 可选校准结果选择主通道；返回 (primary, meta)。"""
    if not suggestions:
        raise ValueError("无条件单建议")

    by_ch = {s.get("channel"): s for s in suggestions}
    meta: dict[str, Any] = {"heuristic": True, "rules": []}

    # 1) 校准建议（若样本尚可）
    cal_pref = (calibration or {}).get("suggested_primary")
    cal_item = ((calibration or {}).get("by_kind") or {}).get(cal_pref or "")
    if (
        cal_pref
        and cal_pref in by_ch
        and isinstance(cal_item, dict)
        and cal_item.get("ok")
        and int(cal_item.get("decisions") or 0) >= 8
        and cal_item.get("quality") not in {"low_sample"}
    ):
        meta["rules"].append(f"calibration_prefers:{cal_pref}")
        meta["source"] = "calibration"
        return by_ch[cal_pref], meta

    # 2) 状态启发式
    if energy_rank in {"thrust_up", "thrust_down"} and "donchian" in by_ch:
        if width_rank == "wide" or energy_rank == "thrust_up":
            meta["rules"].append("regime:trend_like→donchian")
            meta["source"] = "regime_heuristic"
            return by_ch["donchian"], meta
    if energy_rank in {"dissipated", "cooling", "quiet"} and width_rank in {
        "narrow",
        "neutral",
        None,
    }:
        for pref in ("vwreg", "reg"):
            if pref in by_ch:
                meta["rules"].append(f"regime:mean_reversion_like→{pref}")
                meta["source"] = "regime_heuristic"
                return by_ch[pref], meta

    for pref in ("vwreg", "reg", "donchian", "hl"):
        if pref in by_ch:
            meta["rules"].append(f"default_pref:{pref}")
            meta["source"] = "default"
            return by_ch[pref], meta
    meta["source"] = "fallback"
    return suggestions[0], meta
