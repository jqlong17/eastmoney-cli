"""通道计算与东方财富条件单参考价（只读建议，不自动下单）。"""

from __future__ import annotations

from typing import Any

CHANNEL_ALIASES = {
    "none": "none",
    "off": "none",
    "reg": "reg",
    "regression": "reg",
    "linreg": "reg",
    "vwreg": "vwreg",
    "vw": "vwreg",
    "vwlinreg": "vwreg",
    "volreg": "vwreg",
    "donchian": "donchian",
    "dc": "donchian",
    "hl": "hl",
    "highlow": "hl",
    "range": "hl",
}

CHANNEL_COLORS = {
    "reg": "#1f4e79",
    "vwreg": "#0b6e4f",
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
                f"未知通道 {part!r}，可选 none/reg/vwreg/donchian/hl"
                "（可组合，如 vwreg,donchian）"
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


def _vw_linreg_channel(
    closes: list[float],
    volumes: list[float],
    *,
    width: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    """成交量加权线性回归通道：放量 K 线对中轴/带宽影响更大。"""
    n = len(closes)
    if n < 3:
        return closes[:], closes[:], closes[:]
    if len(volumes) != n:
        return _linreg_channel(closes, width=width)

    # 用 sqrt(volume) 缓和极端放量；零量给极小权重避免退化。
    weights = [max((max(0.0, float(v)) ** 0.5), 1e-6) for v in volumes]
    w_sum = sum(weights) or float(n)
    xs = list(range(n))
    x_mean = sum(w * x for w, x in zip(weights, xs)) / w_sum
    y_mean = sum(w * y for w, y in zip(weights, closes)) / w_sum
    num = sum(w * (x - x_mean) * (y - y_mean) for w, x, y in zip(weights, xs, closes))
    den = sum(w * (x - x_mean) ** 2 for w, x in zip(weights, xs)) or 1.0
    slope = num / den
    intercept = y_mean - slope * x_mean
    mid = [intercept + slope * x for x in xs]
    resid = [y - m for y, m in zip(closes, mid)]
    var = sum(w * r * r for w, r in zip(weights, resid)) / max(w_sum - 1.0, 1.0)
    std = var ** 0.5
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
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    hi = max(highs)
    lo = min(lows)
    mid_v = (hi + lo) / 2.0
    n = len(bars)
    return [mid_v] * n, [hi] * n, [lo] * n


def auto_segment_size(n: int, interval: str = "5m", explicit: int = 0) -> int:
    """自动分段长度：优先用 explicit；否则按周期估约 1～2 个交易日。"""
    if explicit > 0:
        return max(8, min(explicit, n))
    # 5 分钟一天约 48 根；15m≈16；30m≈8；60m≈4；日线用更长窗口。
    per_day = {
        "1m": 240,
        "5m": 48,
        "15m": 16,
        "30m": 8,
        "60m": 4,
        "1d": 20,
        "day": 20,
        "1w": 8,
        "week": 8,
        "1mo": 6,
        "month": 6,
    }.get((interval or "5m").lower(), 48)
    # 目标大约 4～8 段，单段约 1～2 个交易日。
    target_segments = 6
    size = max(per_day, (n + target_segments - 1) // target_segments)
    size = min(size, max(8, n))
    return size


def iter_segments(n: int, segment_size: int) -> list[tuple[int, int]]:
    """把 [0,n) 切成连续区间；末段过短则并入前一段。"""
    if n <= 0:
        return []
    size = max(8, min(segment_size, n))
    bounds: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        bounds.append((start, end))
        start = end
    if len(bounds) >= 2:
        s, e = bounds[-1]
        if (e - s) < size // 3:
            prev_s, _ = bounds[-2]
            bounds[-2] = (prev_s, e)
            bounds.pop()
    return bounds


def relative_width_pct(upper: float, lower: float, mid: float) -> float:
    """相对宽度（%）=(上轨−下轨)/|中轴|×100；窄=分歧小/确定性偏高。"""
    denom = abs(float(mid))
    if denom < 1e-9:
        denom = max(abs(float(upper)), abs(float(lower)), 1e-9)
    return (float(upper) - float(lower)) / denom * 100.0


def _percentile(sorted_vals: list[float], q: float) -> float:
    """线性插值分位数；q∈[0,1]。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    q = min(1.0, max(0.0, q))
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _certainty_from_width(
    last_pct: float,
    series_pct: list[float],
    *,
    causal: bool = True,
) -> dict[str, Any]:
    """用当前宽度相对历史分位，给出描述性分档（研究用，非预测概率）。

    causal=True：阈值只用「当前点之前」的样本，避免同窗自证。
    """
    clean_all = [float(v) for v in series_pct if v is not None and v >= 0]
    if not clean_all:
        return {
            "label": "未知",
            "rank": "unknown",
            "certainty_score": None,
            "note": "宽度序列为空，无法评估",
            "causal": causal,
        }

    if causal and len(clean_all) >= 8:
        hist = clean_all[:-1] if len(clean_all) > 1 else clean_all
    else:
        hist = clean_all
    clean = sorted(hist)

    p33 = _percentile(clean, 0.33)
    p50 = _percentile(clean, 0.50)
    p66 = _percentile(clean, 0.66)
    below = sum(1 for v in clean if v < last_pct)
    equal = sum(1 for v in clean if v == last_pct)
    rank_frac = (below + 0.5 * equal) / len(clean)
    score = round(max(0.0, min(100.0, (1.0 - rank_frac) * 100.0)), 1)

    if last_pct <= p33:
        label, rank = "窄·相对历史偏低", "narrow"
        note = "相对历史宽度偏窄（描述性）：轨位更清晰的叙事更强；仍非胜率。"
    elif last_pct >= p66:
        label, rank = "宽·相对历史偏高", "wide"
        note = "相对历史宽度偏高（描述性）：触发带噪声可能更大；勿把几何盈亏比当期望。"
    else:
        label, rank = "中性·相对历史中位", "neutral"
        note = "宽度处于历史中位附近（描述性）；需结合日线与回放校准。"

    return {
        "label": label,
        "rank": rank,
        "certainty_score": score,
        "score_note": "分位反转得分，仅描述相对窄/宽，不是预测概率",
        "p33_pct": round(p33, 3),
        "p50_pct": round(p50, 3),
        "p66_pct": round(p66, 3),
        "note": note,
        "causal": causal,
        "hist_n": len(clean),
    }


def _channel_on_slice(
    bars: list[dict[str, Any]],
    *,
    kind: str,
    width: float,
    global_start: int,
    segment_index: int,
) -> dict[str, Any]:
    closes = [float(b["close"]) for b in bars]
    volumes = [float(b.get("volume") or 0) for b in bars]
    n = len(bars)
    if kind == "reg":
        mid_s, up_s, lo_s = _linreg_channel(closes, width=width)
        label = f"回归±{width:g}σ"
    elif kind == "vwreg":
        mid_s, up_s, lo_s = _vw_linreg_channel(closes, volumes, width=width)
        label = f"价量回归±{width:g}σ"
    elif kind == "donchian":
        mid_s, up_s, lo_s = _donchian_channel(bars, window=n)
        label = f"Donchian({n})"
    elif kind == "hl":
        mid_s, up_s, lo_s = _hl_channel(bars)
        label = f"高低区间/{n}"
    else:
        raise ValueError(f"未知通道: {kind}")

    width_abs = [float(u) - float(lo) for u, lo in zip(up_s, lo_s)]
    width_pct = [
        relative_width_pct(u, lo, m) for u, lo, m in zip(up_s, lo_s, mid_s)
    ]

    return {
        "kind": kind,
        "label": label if segment_index == 0 else f"{label}#{segment_index + 1}",
        "color": CHANNEL_COLORS.get(kind, "#333333"),
        "start": global_start,
        "end": global_start + n,
        "segment_index": segment_index,
        "mid": mid_s,
        "upper": up_s,
        "lower": lo_s,
        "width_abs": width_abs,
        "width_pct": width_pct,
        "last_mid": round_price(mid_s[-1]),
        "last_upper": round_price(up_s[-1]),
        "last_lower": round_price(lo_s[-1]),
        "last_width_abs": round(width_abs[-1], 4),
        "last_width_pct": round(width_pct[-1], 3),
    }


def compute_channel_segments(
    bars: list[dict[str, Any]],
    *,
    kind: str,
    window: int = 0,
    width: float = 2.0,
    interval: str = "5m",
    full_range: bool = True,
) -> list[dict[str, Any]]:
    """计算通道。

    full_range=True：自动分段覆盖全部时间；
    full_range=False：仅最近一个 window（兼容旧行为）。
    """
    n = len(bars)
    if n < 3:
        raise ValueError("K 线不足 3 根，无法计算通道")

    if not full_range:
        use_n = n if window <= 0 else min(window, n)
        start = n - use_n
        seg = _channel_on_slice(
            bars[start:],
            kind=kind,
            width=width,
            global_start=start,
            segment_index=0,
        )
        return [seg]

    size = auto_segment_size(n, interval=interval, explicit=window)
    segments: list[dict[str, Any]] = []
    for idx, (start, end) in enumerate(iter_segments(n, size)):
        if end - start < 3:
            continue
        segments.append(
            _channel_on_slice(
                bars[start:end],
                kind=kind,
                width=width,
                global_start=start,
                segment_index=idx,
            )
        )
    return segments


def compute_channel(
    bars: list[dict[str, Any]],
    *,
    kind: str,
    window: int = 0,
    width: float = 2.0,
    interval: str = "5m",
    full_range: bool = True,
) -> dict[str, Any]:
    """兼容旧接口：返回最后一段，并附带全部 segments。"""
    segments = compute_channel_segments(
        bars,
        kind=kind,
        window=window,
        width=width,
        interval=interval,
        full_range=full_range,
    )
    last = segments[-1]
    return {
        **last,
        "segments": segments,
        "label": f"{last['label']}×{len(segments)}段" if full_range and len(segments) > 1 else last["label"],
    }


def stitch_width_series(
    n_bars: int,
    segments: list[dict[str, Any]],
) -> tuple[list[float | None], list[float | None]]:
    """把分段 width_pct / width_abs 拼成与 bars 对齐的全序列。"""
    pct: list[float | None] = [None] * n_bars
    abs_w: list[float | None] = [None] * n_bars
    for seg in segments:
        start = int(seg["start"])
        end = int(seg["end"])
        wp = seg.get("width_pct") or []
        wa = seg.get("width_abs") or []
        for i, (p, a) in enumerate(zip(wp, wa)):
            idx = start + i
            if idx >= end or idx >= n_bars:
                break
            pct[idx] = float(p)
            abs_w[idx] = float(a)
    return pct, abs_w


def compute_channel_width(
    bars: list[dict[str, Any]],
    *,
    kind: str,
    window: int = 0,
    width: float = 2.0,
    interval: str = "5m",
    full_range: bool = True,
) -> dict[str, Any]:
    """通道宽度序列 + 确定性评估。

    相对宽度 width_pct = (上轨−下轨)/|中轴|×100：
    - 窄 → 市场分歧小、确定性偏高
    - 宽 → 波动/分歧大、不确定性偏高
    """
    packed = compute_channel(
        bars,
        kind=kind,
        window=window,
        width=width,
        interval=interval,
        full_range=full_range,
    )
    segments = packed.get("segments") or [packed]
    n = len(bars)
    series_pct, series_abs = stitch_width_series(n, segments)
    valid_pct = [float(v) for v in series_pct if v is not None]
    last_pct = float(packed.get("last_width_pct") or (valid_pct[-1] if valid_pct else 0.0))
    last_abs = float(packed.get("last_width_abs") or (series_abs[-1] or 0.0))
    certainty = _certainty_from_width(last_pct, valid_pct)
    mean_pct = sum(valid_pct) / len(valid_pct) if valid_pct else 0.0
    return {
        "kind": kind,
        "label": packed["label"],
        "color": packed.get("color") or CHANNEL_COLORS.get(kind, "#333333"),
        "segments": segments,
        "series_pct": series_pct,
        "series_abs": series_abs,
        "last_width_pct": round(last_pct, 3),
        "last_width_abs": round(last_abs, 4),
        "mean_width_pct": round(mean_pct, 3),
        "min_width_pct": round(min(valid_pct), 3) if valid_pct else None,
        "max_width_pct": round(max(valid_pct), 3) if valid_pct else None,
        "certainty": certainty,
        "last_mid": packed["last_mid"],
        "last_upper": packed["last_upper"],
        "last_lower": packed["last_lower"],
    }


def assess_width_for_plan(
    width_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """综合多通道宽度，给条件单「是否更合理」的研究备注。"""
    if not width_reports:
        return {
            "rank": "unknown",
            "label": "未知",
            "certainty_score": None,
            "plan_hint": "无宽度数据",
            "channels": [],
        }

    # 优先用价量回归，其次回归，再 Donchian
    preferred = None
    for pref in ("vwreg", "reg", "donchian", "hl"):
        for item in width_reports:
            if item.get("kind") == pref:
                preferred = item
                break
        if preferred:
            break
    preferred = preferred or width_reports[0]
    cert = preferred.get("certainty") or {}
    rank = cert.get("rank") or "unknown"
    score = cert.get("certainty_score")

    if rank == "narrow":
        plan_hint = (
            "宽度相对历史偏窄（描述性）：几何轨位更清楚，但仍需看回放命中率与止损。"
        )
        reasonableness = "轨位更清晰（未验证期望）"
    elif rank == "wide":
        plan_hint = (
            "宽度相对历史偏宽（描述性）：几何盈亏比易虚高；宜参考校准层或缩小仓位。"
        )
        reasonableness = "波动偏大（未验证期望）"
    elif rank == "neutral":
        plan_hint = "宽度中性（描述性）：以日线方向 + 回放校准为主。"
        reasonableness = "描述中性"
    else:
        plan_hint = "宽度状态未知。"
        reasonableness = "未知"

    return {
        "primary_kind": preferred.get("kind"),
        "primary_label": preferred.get("label"),
        "rank": rank,
        "label": cert.get("label") or "未知",
        "certainty_score": score,
        "last_width_pct": preferred.get("last_width_pct"),
        "mean_width_pct": preferred.get("mean_width_pct"),
        "reasonableness": reasonableness,
        "plan_hint": plan_hint,
        "note": cert.get("note"),
        "channels": [
            {
                "kind": w.get("kind"),
                "label": w.get("label"),
                "last_width_pct": w.get("last_width_pct"),
                "certainty_score": (w.get("certainty") or {}).get("certainty_score"),
                "rank": (w.get("certainty") or {}).get("rank"),
                "label_certainty": (w.get("certainty") or {}).get("label"),
            }
            for w in width_reports
        ],
    }


def _suggestion_from_rails(
    kind: str,
    *,
    lower: float,
    mid: float,
    upper: float,
    last_close: float,
    label: str,
) -> dict[str, Any]:
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
    elif kind == "vwreg":
        style = "价量回归·放量确认"
        buy = {
            "用途": "放量回踩买入（触价）",
            "建议触发价": lower,
            "说明": "价量加权下轨；宜配合放量/缩量回踩观察，条件单「价格小于等于」",
        }
        sell = {
            "用途": "放量冲高卖出（触价）",
            "建议触发价": upper,
            "说明": "价量加权上轨；宜关注上轨附近放量滞涨，条件单「价格大于等于」",
        }
        stop = {
            "用途": "放量跌破止损（可选）",
            "建议触发价": round_price(lower - pad),
            "说明": "下轨下方；若伴随放量跌破，防守意义更强",
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
    return {
        "channel": kind,
        "label": label,
        "style": style,
        "last_lower": lower,
        "last_mid": mid,
        "last_upper": upper,
        "buy": buy,
        "sell": sell,
        "stop": stop,
        "buy_price": buy["建议触发价"],
        "sell_price": sell["建议触发价"],
        "stop_price": stop["建议触发价"],
    }


def suggest_condition_orders(
    kline: dict[str, Any],
    *,
    channels: list[str] | None = None,
    channel_window: int = 0,
    channel_width: float = 2.0,
    full_range: bool = True,
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
    width_reports: list[dict[str, Any]] = []

    for kind in kinds:
        width_info = compute_channel_width(
            bars,
            kind=kind,
            window=channel_window,
            width=channel_width,
            interval=str(kline.get("interval") or "5m"),
            full_range=full_range,
        )
        item = _suggestion_from_rails(
            kind,
            lower=width_info["last_lower"],
            mid=width_info["last_mid"],
            upper=width_info["last_upper"],
            last_close=last_close,
            label=width_info["label"],
        )
        item["last_width_pct"] = width_info["last_width_pct"]
        item["last_width_abs"] = width_info["last_width_abs"]
        item["certainty"] = width_info["certainty"]
        suggestions.append(item)
        width_reports.append(width_info)

    width_assessment = assess_width_for_plan(width_reports)

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
        "width_assessment": width_assessment,
    }
