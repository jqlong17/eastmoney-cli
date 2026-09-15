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

    # 因果分位：阈值只用当前点之前，避免同窗自证
    hist = ke[:-1] if len(ke) > 8 else ke
    valid = sorted(k for k in hist if k >= 0)
    last_ke = ke[-1]
    last_v = velocity[-1]
    last_m = mass[-1]
    last_signed = signed_ke[-1]
    p33 = _percentile(valid, 0.33) if valid else 0.0
    p50 = _percentile(valid, 0.50) if valid else 0.0
    p66 = _percentile(valid, 0.66) if valid else 0.0
    p90 = _percentile(valid, 0.90) if valid else 0.0

    # 近端衰减：最近动能相对前一段均值（不含未来）
    tail = max(5, n // 20)
    prev = ke[max(0, n - 2 * tail) : max(1, n - tail)] or ke[: max(1, n // 2)]
    prev_mean = sum(prev) / len(prev) if prev else last_ke
    decaying = bool(prev_mean > 1e-9 and last_ke < 0.55 * prev_mean)

    if last_ke >= p66 and abs(last_v) > 0:
        if last_v > 0:
            rank, label = "thrust_up", "上攻推进"
            note = "动能相对历史偏高且向上（描述性隐喻，未验证 alpha）。"
            plan_hint = "状态偏趋势上攻：突破叙事强于回踩；仍以回放校准为准。"
        else:
            rank, label = "thrust_down", "下破推进"
            note = "动能相对历史偏高且向下（描述性）。"
            plan_hint = "状态偏下跌推进：防守优先，不宜仅因下轨便宜挂买单。"
        reasonableness = "强动能状态（描述性）"
    elif last_ke <= p33:
        rank, label = "dissipated", "动能耗散"
        note = "动能相对历史偏低（描述性）。"
        plan_hint = "动能耗散：更适合等结构/收敛；结合宽度与校准。"
        reasonableness = "低动能状态（描述性）"
    else:
        if decaying:
            rank, label = "cooling", "冲量回落"
            note = "动能从近端高位回落（描述性）。"
            plan_hint = "冲量回落：回踩类 setup 的叙事窗口；需校准确认。"
            reasonableness = "回落状态（描述性）"
        else:
            rank, label = "neutral", "动能中性"
            note = "动能处于历史中位附近（描述性）。"
            plan_hint = "动能中性：以通道轨位 + 回放校准为主。"
            reasonableness = "中性（描述性）"

    below = sum(1 for v in valid if v < last_ke)
    equal = sum(1 for v in valid if v == last_ke)
    rank_frac = (below + 0.5 * equal) / max(len(valid), 1)
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
            "operability_note": "启发式可操作性，非期望收益",
            "plan_hint": plan_hint,
            "note": note,
            "decaying": decaying,
            "causal": True,
        },
    }
