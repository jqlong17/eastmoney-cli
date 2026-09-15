"""价量「动能」指标（物理隐喻，研究用）。

质量 m ≈ 相对成交量（volume / 量均）
速度 v ≈ 收益（Δclose/close）
动能 KE ≈ ½ m v²

用于评估波段推进/耗散，辅助条件单「挂不挂、是否谨慎」。
"""

from __future__ import annotations

from typing import Any

from .levels import round_price


def _sma(values: list[float], window: int) -> list[float]:
    n = len(values)
    w = max(1, min(window, n))
    out: list[float] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= w:
            running -= values[i - w]
            out.append(running / w)
        else:
            out.append(running / (i + 1))
    return out


def _percentile(sorted_vals: list[float], q: float) -> float:
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


def compute_kinetic_energy(
    bars: list[dict[str, Any]],
    *,
    vol_ma: int = 20,
    vel_smooth: int = 3,
) -> dict[str, Any]:
    """计算动能序列与分档评估。

    Returns
    -------
    mass, velocity, ke, signed_ke, assessment, ...
    """
    n = len(bars)
    if n < 3:
        raise ValueError("K 线不足 3 根，无法计算动能")

    closes = [float(b["close"]) for b in bars]
    volumes = [max(float(b.get("volume") or 0), 0.0) for b in bars]
    vol_ma_s = _sma(volumes, vol_ma)

    # 速度：单根收益；可选短窗平滑，降低 5m 噪声（波段用）
    raw_v = [0.0]
    for i in range(1, n):
        prev = closes[i - 1]
        if abs(prev) < 1e-12:
            raw_v.append(0.0)
        else:
            raw_v.append((closes[i] - prev) / prev)

    if vel_smooth > 1:
        velocity = _sma(raw_v, vel_smooth)
    else:
        velocity = raw_v

    mass: list[float] = []
    ke: list[float] = []
    signed_ke: list[float] = []
    momentum: list[float] = []
    for m_vol, v, vma in zip(volumes, velocity, vol_ma_s):
        m = m_vol / max(vma, 1e-9)
        # 截断极端放量，避免一字板/异常量淹没尺度
        m = min(max(m, 0.0), 8.0)
        energy = 0.5 * m * (v * v)
        # 用万分比尺度，便于读图（v 通常 1e-3 量级）
        energy_bp2 = energy * 1e8
        mass.append(m)
        ke.append(energy_bp2)
        signed_ke.append(energy_bp2 if v >= 0 else -energy_bp2)
        momentum.append(m * v * 1e4)  # 动量（相对量×收益×1e4）

    valid = sorted(k for k in ke if k >= 0)
    last_ke = ke[-1]
    last_v = velocity[-1]
    last_m = mass[-1]
    last_signed = signed_ke[-1]
    p33 = _percentile(valid, 0.33)
    p50 = _percentile(valid, 0.50)
    p66 = _percentile(valid, 0.66)
    p90 = _percentile(valid, 0.90)

    # 近端衰减：最近动能相对前一段均值
    tail = max(5, n // 20)
    prev = ke[max(0, n - 2 * tail) : max(1, n - tail)] or ke[: max(1, n // 2)]
    prev_mean = sum(prev) / len(prev) if prev else last_ke
    decaying = bool(prev_mean > 1e-9 and last_ke < 0.55 * prev_mean)

    if last_ke >= p66 and abs(last_v) > 0:
        if last_v > 0:
            rank, label = "thrust_up", "上攻推进"
            note = "动能偏高且向上：有量参与的上攻；条件单偏突破/持有叙事，回踩单需等动能回落。"
            plan_hint = "推进偏多：突破类条件单更顺；回踩买入宜等动能衰减后再挂。"
        else:
            rank, label = "thrust_down", "下破推进"
            note = "动能偏高且向下：有量参与的下跌；防守优先，不宜抢反弹条件单。"
            plan_hint = "推进偏空：优先止损/观望，勿因下轨便宜就挂激进买单。"
        reasonableness = "看方向（强动能）"
    elif last_ke <= p33:
        if decaying or last_ke <= p33:
            rank, label = "dissipated", "动能耗散"
            note = "动能偏低：推进力弱，价格易在通道内空转；更适合等回踩/收敛后再挂单。"
            plan_hint = "动能耗散：通道内噪声相对大，条件单宜配合窄宽度或更清晰的日线方向。"
            reasonableness = "宜等结构（低动能）"
        else:
            rank, label = "quiet", "动能平静"
            note = "动能安静。"
            plan_hint = "动能平静，按通道轨位与宽度评估即可。"
            reasonableness = "可参考"
    else:
        if decaying:
            rank, label = "cooling", "冲量回落"
            note = "动能从高位回落：冲量衰减，若靠近下轨可观察回踩；若刚破位则防续跌失败反抽。"
            plan_hint = "冲量回落：回踩类条件单窗口更好；假突破后也更常见。"
            reasonableness = "回踩窗口偏好"
        else:
            rank, label = "neutral", "动能中性"
            note = "动能处于中位，需结合通道宽度与日线方向。"
            plan_hint = "动能中性：以价量通道轨位 + 宽度确定性为主。"
            reasonableness = "可参考"

    # 分数：适度动能（非极端空转、非疯狂单向）略高；这里给「可操作性」分
    below = sum(1 for v in valid if v < last_ke)
    equal = sum(1 for v in valid if v == last_ke)
    rank_frac = (below + 0.5 * equal) / max(len(valid), 1)
    # 可操作性：耗散且非单边崩盘时偏高；极端高动能略降（难挂回踩）
    if rank in {"dissipated", "cooling", "quiet"}:
        operability = round(min(100.0, 55 + (1 - rank_frac) * 40), 1)
    elif rank in {"thrust_up", "thrust_down"}:
        operability = round(max(15.0, 70 - rank_frac * 50), 1)
    else:
        operability = round(50 + (0.5 - abs(rank_frac - 0.5)) * 40, 1)

    return {
        "vol_ma": vol_ma,
        "vel_smooth": vel_smooth,
        "mass": mass,
        "velocity": velocity,
        "ke": ke,
        "signed_ke": signed_ke,
        "momentum": momentum,
        "last_ke": round(last_ke, 4),
        "last_signed_ke": round(last_signed, 4),
        "last_mass": round(last_m, 3),
        "last_velocity": round(last_v, 6),
        "last_close": round_price(closes[-1]),
        "p33": round(p33, 4),
        "p50": round(p50, 4),
        "p66": round(p66, 4),
        "p90": round(p90, 4),
        "decaying": decaying,
        "assessment": {
            "rank": rank,
            "label": label,
            "reasonableness": reasonableness,
            "operability_score": operability,
            "plan_hint": plan_hint,
            "note": note,
            "decaying": decaying,
        },
    }
