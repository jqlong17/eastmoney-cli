"""通道计算与东方财富条件单参考价（只读建议，不自动下单）。"""

from __future__ import annotations

from typing import Any

CHANNEL_ALIASES = {
    "none": "none",
    "off": "none",
    "reg": "reg",
    "regression": "reg",
    "linreg": "reg",
    "donchian": "donchian",
    "dc": "donchian",
    "hl": "hl",
    "highlow": "hl",
    "range": "hl",
}

CHANNEL_COLORS = {
    "reg": "#1f4e79",
    "donchian": "#8b5a2b",
    "hl": "#6a1b9a",
}


def parse_channels(spec: str) -> list[str]:
    """解析 --channel：支持 reg,donchian 或 reg+hl。"""
    raw = (spec or "none").strip().lower()
    if not raw:
        return ["none"]
    parts = [p.strip() for p in raw.replace("+", ",").replace("|", ",").split(",") if p.strip()]
    out: list[str] = []
    for part in parts:
        if part not in CHANNEL_ALIASES:
            raise ValueError(
                f"未知通道 {part!r}，可选 none/reg/donchian/hl（可组合，如 reg,donchian）"
            )
        kind = CHANNEL_ALIASES[part]
        if kind == "none":
            return ["none"]
        if kind not in out:
            out.append(kind)
    return out or ["none"]


def round_price(price: float) -> float:
    """A 股常用两位小数，便于填条件单。"""
    return round(float(price) + 1e-10, 2)


def _linreg_channel(
    closes: list[float],
    *,
    width: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    n = len(closes)
    if n < 3:
        return closes[:], closes[:], closes[:]
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(closes) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, closes))
    den = sum((x - x_mean) ** 2 for x in xs) or 1.0
    slope = num / den
    intercept = y_mean - slope * x_mean
    mid = [intercept + slope * x for x in xs]
    resid = [y - m for y, m in zip(closes, mid)]
    std = (sum(r * r for r in resid) / max(n - 1, 1)) ** 0.5
    upper = [m + width * std for m in mid]
    lower = [m - width * std for m in mid]
    return mid, upper, lower


def _donchian_channel(
    bars: list[dict[str, Any]],
    *,
    window: int,
) -> tuple[list[float], list[float], list[float]]:
    n = len(bars)
    w = max(2, min(window, n))
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    upper: list[float] = []
    lower: list[float] = []
    mid: list[float] = []
    for i in range(n):
        lo = max(0, i - w + 1)
        hi = max(highs[lo : i + 1])
        lw = min(lows[lo : i + 1])
        upper.append(hi)
        lower.append(lw)
        mid.append((hi + lw) / 2.0)
    return mid, upper, lower


def _hl_channel(bars: list[dict[str, Any]]) -> tuple[list[float], list[float], list[float]]:
    """窗口内最高/最低水平通道。"""
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    hi = max(highs)
    lo = min(lows)
    mid_v = (hi + lo) / 2.0
    n = len(bars)
    return [mid_v] * n, [hi] * n, [lo] * n


def compute_channel(
    bars: list[dict[str, Any]],
    *,
    kind: str,
    window: int = 0,
    width: float = 2.0,
) -> dict[str, Any]:
    n = len(bars)
    if n < 3:
        raise ValueError("K 线不足 3 根，无法计算通道")
    closes = [float(b["close"]) for b in bars]
    use_n = n if window <= 0 else min(window, n)
    start = n - use_n
    seg = bars[start:]
    seg_closes = closes[start:]

    if kind == "reg":
        mid_s, up_s, lo_s = _linreg_channel(seg_closes, width=width)
        label = f"回归±{width:g}σ/{use_n}"
    elif kind == "donchian":
        dc_win = use_n if window > 0 else min(48, use_n)
        mid_s, up_s, lo_s = _donchian_channel(seg, window=dc_win)
        label = f"Donchian({dc_win})"
    elif kind == "hl":
        mid_s, up_s, lo_s = _hl_channel(seg)
        label = f"高低区间/{use_n}"
    else:
        raise ValueError(f"未知通道: {kind}")

    return {
        "kind": kind,
        "label": label,
        "color": CHANNEL_COLORS.get(kind, "#333333"),
        "window": use_n,
        "start": start,
        "mid": [None] * start + mid_s,
        "upper": [None] * start + up_s,
        "lower": [None] * start + lo_s,
        "last_mid": round_price(mid_s[-1]),
        "last_upper": round_price(up_s[-1]),
        "last_lower": round_price(lo_s[-1]),
    }


def suggest_condition_orders(
    kline: dict[str, Any],
    *,
    channels: list[str] | None = None,
    channel_window: int = 0,
    channel_width: float = 2.0,
) -> dict[str, Any]:
    """按通道轨位给出可填入东财条件单的参考价（研究用，不下单）。"""
    bars = kline.get("bars") or []
    if len(bars) < 3:
        raise ValueError("K 线不足，无法给出条件单参考价")

    kinds = [k for k in (channels or ["reg"]) if k != "none"]
    if not kinds:
        kinds = ["reg"]

    last = bars[-1]
    last_close = round_price(last["close"])
    suggestions: list[dict[str, Any]] = []

    for kind in kinds:
        ch = compute_channel(bars, kind=kind, window=channel_window, width=channel_width)
        lower, mid, upper = ch["last_lower"], ch["last_mid"], ch["last_upper"]
        pad = max(round_price(last_close * 0.002), 0.01)

        if kind == "reg":
            style = "回归通道·高抛低吸"
            buy = {
                "用途": "回调买入（触价）",
                "建议触发价": lower,
                "说明": "接近回归下轨；东财条件单可选「价格小于等于」",
            }
            sell = {
                "用途": "反弹卖出（触价）",
                "建议触发价": upper,
                "说明": "接近回归上轨；东财条件单可选「价格大于等于」",
            }
            stop = {
                "用途": "防守止损（可选）",
                "建议触发价": round_price(lower - pad),
                "说明": "下轨再下方一点；跌破可能趋势转弱",
            }
        elif kind == "donchian":
            style = "Donchian·突破/跌破"
            buy = {
                "用途": "向上突破买入",
                "建议触发价": round_price(upper + pad),
                "说明": "站上近期上轨；条件单「价格大于等于」",
            }
            sell = {
                "用途": "向下跌破卖出",
                "建议触发价": round_price(lower - pad),
                "说明": "跌破近期下轨；条件单「价格小于等于」",
            }
            stop = {
                "用途": "突破失败离场（可选）",
                "建议触发价": mid,
                "说明": "买入后若回到中轴附近，可视为突破失败",
            }
        else:
            style = "高低区间·区间交易"
            buy = {
                "用途": "区间下沿买入",
                "建议触发价": lower,
                "说明": "窗口最低附近；条件单「价格小于等于」",
            }
            sell = {
                "用途": "区间上沿卖出",
                "建议触发价": upper,
                "说明": "窗口最高附近；条件单「价格大于等于」",
            }
            stop = {
                "用途": "跌破区间止损（可选）",
                "建议触发价": round_price(lower - pad),
                "说明": "跌破窗口最低再下方",
            }

        suggestions.append(
            {
                "channel": kind,
                "label": ch["label"],
                "style": style,
                "last_lower": lower,
                "last_mid": mid,
                "last_upper": upper,
                "buy": buy,
                "sell": sell,
                "stop": stop,
            }
        )

    return {
        "symbol": kline.get("symbol") or "",
        "code": kline.get("code") or "",
        "name": kline.get("name") or "",
        "interval": kline.get("interval") or "",
        "last_time": last.get("time"),
        "last_close": last_close,
        "disclaimer": (
            "仅为基于公开行情通道的研究参考价，不是投资建议；"
            "请人工录入东方财富条件单，本工具不下单、不连接交易。"
        ),
        "suggestions": suggestions,
    }
