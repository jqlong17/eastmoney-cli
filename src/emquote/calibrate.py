"""条件单历史校准（事件研究，walk-forward，研究用）。

事件定义（与东财条件单叙事对齐）：
1. 决策时刻 t：仅用 bars[t-W+1 : t+1] 估计通道轨（无未来段内信息）。
2. 有效期 H 根 K 线内等待买入区触达（用 high/low 触价）。
3. 成交后，在剩余有效期内看先触止盈还是先触止损。
4. 结果：tp_first / stop_first / expire_unfilled / timeout_after_fill。

轨位与触达模拟见 touch.py（与 plan 共享）。
输出频率 + Beta-Binomial 弱先验收缩后验（经验贝叶斯味道）。
"""

from __future__ import annotations

from typing import Any

from .risk import net_risk_reward, round_trip_cost_pct
from .touch import build_rails, simulate_touch


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
    n = max(int(n), 0)
    wins = max(0, min(int(wins), n)) if n else 0
    post_a = a + wins
    post_b = b + max(n - wins, 0)
    mean = post_a / (post_a + post_b)
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


def _count_outcomes(outcomes: list[str]) -> dict[str, int]:
    counts = {
        "tp_first": 0,
        "stop_first": 0,
        "expire_unfilled": 0,
        "timeout_after_fill": 0,
    }
    for o in outcomes:
        if o in counts:
            counts[o] += 1
    return counts


def _rates_from_counts(
    counts: dict[str, int],
    *,
    prior_a: float = 2.0,
    prior_b: float = 2.0,
) -> dict[str, Any]:
    decisions = sum(counts.values())
    filled = counts["tp_first"] + counts["stop_first"] + counts["timeout_after_fill"]
    return {
        "fill": _beta_mean(filled, max(decisions, 1), a=prior_a, b=prior_b),
        "tp_given_fill": _beta_mean(counts["tp_first"], max(filled, 1), a=prior_a, b=prior_b),
        "stop_given_fill": _beta_mean(counts["stop_first"], max(filled, 1), a=prior_a, b=prior_b),
        "tp_vs_all_decisions": _beta_mean(counts["tp_first"], max(decisions, 1), a=prior_a, b=prior_b),
        "expire_unfilled_share": round(counts["expire_unfilled"] / max(decisions, 1), 4),
        "decisions": decisions,
        "filled": filled,
    }


def _walk_outcomes(
    bars: list[dict[str, Any]],
    *,
    kind: str,
    channel_width: float,
    interval: str,
    w: int,
    horizon: int,
    step: int,
    cost_pct: float,
) -> tuple[list[str], list[float]]:
    outcomes: list[str] = []
    filled_net_rs: list[float] = []
    last_t = len(bars) - horizon - 1
    for t in range(w - 1, last_t + 1, step):
        slice_bars = bars[t - w + 1 : t + 1]
        try:
            rails = build_rails(
                slice_bars,
                kind=kind,
                channel_width=channel_width,
                interval=interval,
            )
        except ValueError:
            continue
        if float(rails["buy"]) <= float(rails["stop"]) or float(rails["sell"]) <= float(rails["buy"]):
            continue
        outcome = simulate_touch(bars, t, rails, horizon=horizon)
        outcomes.append(outcome)
        if outcome in {"tp_first", "stop_first"}:
            rr = net_risk_reward(rails["buy"], rails["sell"], rails["stop"], cost_pct=cost_pct)
            if outcome == "tp_first" and rr.get("ratio_net") is not None:
                filled_net_rs.append(float(rr["ratio_net"]))
            else:
                filled_net_rs.append(-1.0)
    return outcomes, filled_net_rs


def _fold_scorecard(outcomes: list[str], *, n_folds: int = 4) -> dict[str, Any]:
    """把决策序列切成时序 fold，输出稳定性成绩单。"""
    n = len(outcomes)
    if n < 4:
        return {
            "ok": False,
            "reason": "决策点过少，不做 fold 切分",
            "n_folds": 0,
            "folds": [],
            "badge": "insufficient",
        }
    folds_n = min(max(int(n_folds), 2), n)
    size = n // folds_n
    folds: list[dict[str, Any]] = []
    tp_means: list[float] = []
    for i in range(folds_n):
        start = i * size
        end = n if i == folds_n - 1 else (i + 1) * size
        chunk = outcomes[start:end]
        if not chunk:
            continue
        c = _count_outcomes(chunk)
        filled = c["tp_first"] + c["stop_first"] + c["timeout_after_fill"]
        fill_rate = filled / len(chunk)
        tp_rate = c["tp_first"] / max(filled, 1)
        stop_rate = c["stop_first"] / max(filled, 1)
        edge = tp_rate - stop_rate
        tp_means.append(tp_rate)
        if filled == 0:
            label = "no_fill"
        elif edge > 0.08:
            label = "tp_lean"
        elif edge < -0.08:
            label = "stop_heavy"
        else:
            label = "mixed"
        folds.append(
            {
                "fold": i + 1,
                "decisions": len(chunk),
                "filled": filled,
                "fill_rate": round(fill_rate, 4),
                "tp_given_fill": round(tp_rate, 4),
                "stop_given_fill": round(stop_rate, 4),
                "edge": round(edge, 4),
                "label": label,
                "counts": c,
            }
        )
    if len(tp_means) >= 2:
        mean_tp = sum(tp_means) / len(tp_means)
        var = sum((x - mean_tp) ** 2 for x in tp_means) / len(tp_means)
        std = var ** 0.5
        signs = {
            1 if float(e.get("edge") or 0) > 0.02 else (-1 if float(e.get("edge") or 0) < -0.02 else 0)
            for e in folds
        }
        sign_flip = len(signs - {0}) > 1
        if std <= 0.08 and not sign_flip:
            badge = "stable"
            note = "各 fold 先触止盈比例接近，时序上不那么飘。"
        elif std <= 0.18:
            badge = "moderate"
            note = "fold 间有波动；看成绩单时勿只盯全样本均值。"
        else:
            badge = "fragile"
            note = "fold 间结论差很大，全样本漂亮也可能是某一段运气。"
        if sign_flip and badge == "stable":
            badge = "moderate"
            note = "edge 符号在 fold 间翻转，稳健性打折。"
    else:
        mean_tp = tp_means[0] if tp_means else None
        std = None
        badge = "insufficient"
        note = "有效 fold 不足。"

    return {
        "ok": True,
        "n_folds": len(folds),
        "folds": folds,
        "tp_given_fill_avg": None if mean_tp is None else round(mean_tp, 4),
        "tp_given_fill_std": None if std is None else round(std, 4),
        "badge": badge,
        "note": note,
        "how_to_read": (
            "fold 按时间顺序切分；badge=stable/moderate/fragile 描述段间一致性，"
            "不是胜率承诺。"
        ),
    }


def calibrate_condition_orders(
    kline: dict[str, Any],
    *,
    kind: str = "vwreg",
    channel_window: int = 96,
    channel_width: float = 2.0,
    validity_days: int = 5,
    step_days: float = 0.5,
    cost_pct: float | None = None,
    n_folds: int = 4,
) -> dict[str, Any]:
    """对单通道风格做 walk-forward 触达校准（含同票时序经验贝叶斯）。"""
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
            "engine": "shared_touch",
        }

    outcomes, filled_net_rs = _walk_outcomes(
        bars,
        kind=kind,
        channel_width=channel_width,
        interval=interval,
        w=w,
        horizon=horizon,
        step=step,
        cost_pct=cost_pct,
    )
    counts = _count_outcomes(outcomes)
    decisions = len(outcomes)
    # 同票时序 EB：前半决策估先验，后半做似然更新（防用全样本又当先验又当似然）
    split = max(1, decisions // 2)
    early = outcomes[:split]
    late = outcomes[split:] if decisions >= 6 else outcomes
    early_c = _count_outcomes(early)
    late_c = _count_outcomes(late)
    early_filled = early_c["tp_first"] + early_c["stop_first"] + early_c["timeout_after_fill"]
    # 先验强度封顶，避免早期噪声主导
    prior_tp_a = 2.0 + min(early_c["tp_first"], 20)
    prior_tp_b = 2.0 + min(max(early_filled - early_c["tp_first"], 0), 20)
    prior_fill_a = 2.0 + min(early_filled, 20)
    prior_fill_b = 2.0 + min(max(len(early) - early_filled, 0), 20)

    if decisions >= 6 and late:
        tp_given_fill = _beta_mean(
            late_c["tp_first"],
            max(late_c["tp_first"] + late_c["stop_first"] + late_c["timeout_after_fill"], 1),
            a=prior_tp_a,
            b=prior_tp_b,
        )
        fill_rate = _beta_mean(
            late_c["tp_first"] + late_c["stop_first"] + late_c["timeout_after_fill"],
            max(len(late), 1),
            a=prior_fill_a,
            b=prior_fill_b,
        )
        stop_given_fill = _beta_mean(
            late_c["stop_first"],
            max(late_c["tp_first"] + late_c["stop_first"] + late_c["timeout_after_fill"], 1),
            a=2.0 + min(early_c["stop_first"], 20),
            b=2.0 + min(max(early_filled - early_c["stop_first"], 0), 20),
        )
        tp_vs_all = _beta_mean(late_c["tp_first"], max(len(late), 1), a=prior_tp_a, b=prior_tp_b)
        eb_mode = "time_series_split"
        expire_share = round(late_c["expire_unfilled"] / max(len(late), 1), 4)
    else:
        rates_flat = _rates_from_counts(counts)
        tp_given_fill = rates_flat["tp_given_fill"]
        stop_given_fill = rates_flat["stop_given_fill"]
        fill_rate = rates_flat["fill"]
        tp_vs_all = rates_flat["tp_vs_all_decisions"]
        expire_share = rates_flat["expire_unfilled_share"]
        eb_mode = "weak_beta_2_2"

    avg_net_r = sum(filled_net_rs) / len(filled_net_rs) if filled_net_rs else None
    folds = _fold_scorecard(outcomes, n_folds=n_folds)

    if decisions < 8:
        hint = "同窗决策点过少，后验很宽，仅供参考，不可当作胜率承诺。"
        quality = "low_sample"
    elif fill_rate["mean"] < 0.15:
        hint = "历史同类挂单成交偏少；有效期或买入区可能过窄/过远。"
        quality = "low_fill"
    elif tp_given_fill["mean"] + 0.05 < stop_given_fill["mean"]:
        hint = "费用后口径下，历史更常先触止损；回踩单宜降仓或等更窄宽度/动能回落。"
        quality = "stop_heavy"
    elif tp_given_fill["mean"] > stop_given_fill["mean"] + 0.05:
        hint = "时序收缩后先触止盈略占优；仍须独立风控，不是未来胜率。"
        quality = "tp_lean"
    else:
        hint = "先触止盈/止损接近；边缘取决于费用、滑点与日线方向过滤。"
        quality = "mixed"

    if folds.get("badge") == "fragile" and quality in {"tp_lean", "mixed"}:
        hint = f"{hint} 另：fold 成绩单显示段间 fragile，全样本优势不可外推。"

    return {
        "ok": True,
        "kind": kind,
        "engine": "shared_touch",
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
            "empirical_bayes": eb_mode,
            "rails_builder": "emquote.touch.build_rails",
            "simulator": "emquote.touch.simulate_touch",
        },
        "cost_pct_round_trip": cost_pct,
        "decisions": decisions,
        "counts": counts,
        "rates": {
            "fill": fill_rate,
            "tp_given_fill": tp_given_fill,
            "stop_given_fill": stop_given_fill,
            "tp_vs_all_decisions": tp_vs_all,
            "expire_unfilled_share": expire_share,
        },
        "empirical_bayes": {
            "mode": eb_mode,
            "early_decisions": len(early),
            "late_decisions": len(late) if decisions >= 6 else decisions,
            "prior_tp": {"alpha": prior_tp_a, "beta": prior_tp_b},
            "note": "前半段决策构造先验，后半段更新；样本少时退回 Beta(2,2)",
        },
        "folds": folds,
        "avg_net_r_when_resolved": None if avg_net_r is None else round(avg_net_r, 4),
        "quality": quality,
        "hint": hint,
        "disclaimer": (
            "单票时序回放 + 弱/经验先验收缩，不是未来胜率；"
            "未完整覆盖涨跌停无法成交、停牌与冲击成本。"
        ),
    }


def _edge_cell(edge: float | None) -> str:
    if edge is None:
        return "  · "
    if edge >= 0.08:
        return f"+{edge:.2f}"
    if edge <= -0.08:
        return f"{edge:.2f}"
    return f"{edge:+.2f}"


def scan_parameter_stability(
    kline: dict[str, Any],
    *,
    kind: str = "vwreg",
    validity_days: int = 5,
    windows: list[int] | None = None,
    widths: list[float] | None = None,
) -> dict[str, Any]:
    """扫描 channel_window × σ 宽度，看触止盈后验是否稳健。"""
    windows = windows or [48, 96, 144]
    widths = widths or [1.5, 2.0, 2.5]
    rows: list[dict[str, Any]] = []
    for w in windows:
        for sigma in widths:
            item = calibrate_condition_orders(
                kline,
                kind=kind,
                channel_window=int(w),
                channel_width=float(sigma),
                validity_days=validity_days,
            )
            if not item.get("ok"):
                rows.append(
                    {
                        "window": w,
                        "width": sigma,
                        "ok": False,
                        "reason": item.get("reason"),
                    }
                )
                continue
            tp = (item.get("rates") or {}).get("tp_given_fill") or {}
            st = (item.get("rates") or {}).get("stop_given_fill") or {}
            fold_badge = ((item.get("folds") or {}).get("badge"))
            rows.append(
                {
                    "window": w,
                    "width": sigma,
                    "ok": True,
                    "decisions": item.get("decisions"),
                    "quality": item.get("quality"),
                    "fold_badge": fold_badge,
                    "tp_mean": tp.get("mean"),
                    "stop_mean": st.get("mean"),
                    "edge": round(float(tp.get("mean") or 0) - float(st.get("mean") or 0), 4),
                    "avg_net_r": item.get("avg_net_r_when_resolved"),
                }
            )
    ok_rows = [r for r in rows if r.get("ok") and r.get("tp_mean") is not None]
    tp_vals = [float(r["tp_mean"]) for r in ok_rows]
    edge_vals = [float(r["edge"]) for r in ok_rows]
    if tp_vals:
        mean_tp = sum(tp_vals) / len(tp_vals)
        var_tp = sum((x - mean_tp) ** 2 for x in tp_vals) / len(tp_vals)
        std_tp = var_tp ** 0.5
        best = max(ok_rows, key=lambda r: float(r.get("edge") or -9))
        worst = min(ok_rows, key=lambda r: float(r.get("edge") or 9))
        if std_tp <= 0.05:
            stability = "stable"
            note = "不同 window/σ 下触止盈后验波动较小，参数不那么脆弱。"
        elif std_tp <= 0.12:
            stability = "moderate"
            note = "参数有一定敏感性；报告结论时注明所用 window/σ。"
        else:
            stability = "fragile"
            note = "参数很敏感：换 window/σ 结论变化大，勿过度解读单组参数。"
    else:
        mean_tp = None
        std_tp = None
        best = None
        worst = None
        stability = "insufficient"
        note = "有效扫描点不足。"

    heat_rows: list[str] = []
    header = "window\\" + "σ".ljust(4) + "  " + "  ".join(f"{s:.1f}".rjust(5) for s in widths)
    heat_rows.append(header)
    for w in windows:
        cells = []
        for sigma in widths:
            hit = next(
                (r for r in rows if r.get("window") == w and r.get("width") == sigma),
                None,
            )
            if not hit or not hit.get("ok"):
                cells.append("  ·  ")
            else:
                cells.append(_edge_cell(hit.get("edge")).rjust(5))
        heat_rows.append(f"{str(w).rjust(8)}  " + "  ".join(cells))

    scorecard = {
        "stability": stability,
        "tp_mean_avg": None if mean_tp is None else round(mean_tp, 4),
        "tp_mean_std": None if std_tp is None else round(std_tp, 4),
        "edge_avg": round(sum(edge_vals) / len(edge_vals), 4) if edge_vals else None,
        "best_edge": (best or {}).get("edge") if best else None,
        "worst_edge": (worst or {}).get("edge") if worst else None,
        "n_ok": len(ok_rows),
        "n_grid": len(rows),
        "badge_zh": {
            "stable": "稳",
            "moderate": "一般",
            "fragile": "脆",
            "insufficient": "样本不足",
        }.get(stability, stability),
        "how_to_read": (
            "热力格为 edge=先止盈后验−先止损后验；"
            "稳=格子差不多；脆=换参数结论大变。"
        ),
    }

    return {
        "kind": kind,
        "engine": "shared_touch",
        "grid": rows,
        "heatmap_ascii": heat_rows,
        "scorecard": scorecard,
        "tp_mean_avg": scorecard["tp_mean_avg"],
        "tp_mean_std": scorecard["tp_mean_std"],
        "edge_avg": scorecard["edge_avg"],
        "best": best,
        "worst": worst,
        "stability": stability,
        "note": note,
        "disclaimer": "参数扫描仍是同票短样本内比较，不是跨市场稳健性证明。",
    }


def calibrate_multi(
    kline: dict[str, Any],
    *,
    kinds: list[str] | None = None,
    channel_window: int = 96,
    channel_width: float = 2.0,
    validity_days: int = 5,
    with_scan: bool = False,
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
        n = float(item.get("decisions") or 0)
        fold_badge = ((item.get("folds") or {}).get("badge")) or ""
        fold_pen = {"fragile": -0.03, "moderate": -0.01}.get(fold_badge, 0.0)
        return float(tp) - float(st) + min(n, 30) / 300.0 + fold_pen

    best = None
    best_s = -1e9
    for k, item in by_kind.items():
        s = score(item)
        if s > best_s:
            best_s = s
            best = k

    scans = None
    if with_scan:
        scans = {
            k: scan_parameter_stability(kline, kind=k, validity_days=validity_days)
            for k in kinds
            if k != "none"
        }

    return {
        "symbol": kline.get("symbol"),
        "name": kline.get("name"),
        "interval": kline.get("interval"),
        "engine": "shared_touch",
        "by_kind": by_kind,
        "suggested_primary": best,
        "parameter_scan": scans,
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


def print_calibration_report(report: dict[str, Any]) -> None:
    """人读版校准报告（含 fold 成绩单与扫描热力）。"""
    print("条件单历史校准（walk-forward · 共享触达引擎 · 研究用）")
    print(
        f"标的: {report.get('name')} {report.get('symbol')}  "
        f"周期: {report.get('interval')}  建议主通道: {report.get('suggested_primary')}"
    )
    print(report.get("note"))
    for kind, item in (report.get("by_kind") or {}).items():
        print()
        print(f"【{kind}】")
        if not item.get("ok"):
            print(f"  跳过: {item.get('reason')}")
            continue
        rates = item.get("rates") or {}
        eb = item.get("empirical_bayes") or {}
        folds = item.get("folds") or {}
        print(
            f"  决策点: {item.get('decisions')}  质量: {item.get('quality')}  "
            f"EB: {eb.get('mode')}  fold徽章: {folds.get('badge')}"
        )
        print(
            f"  成交后验: {rates.get('fill', {}).get('mean')}  "
            f"先止盈|成交: {rates.get('tp_given_fill', {}).get('mean')}  "
            f"CI80={rates.get('tp_given_fill', {}).get('ci80')}  "
            f"先止损|成交: {rates.get('stop_given_fill', {}).get('mean')}"
        )
        print(f"  counts: {item.get('counts')}")
        if folds.get("ok") and folds.get("folds"):
            print("  —— fold 成绩单（时序切分）——")
            print(
                f"  {'fold':>4}  {'n':>4}  {'fill':>6}  {'tp|fill':>7}  "
                f"{'stop|fill':>9}  {'edge':>6}  label"
            )
            for f in folds["folds"]:
                print(
                    f"  {f.get('fold'):>4}  {f.get('decisions'):>4}  "
                    f"{float(f.get('fill_rate') or 0):>6.2f}  "
                    f"{float(f.get('tp_given_fill') or 0):>7.2f}  "
                    f"{float(f.get('stop_given_fill') or 0):>9.2f}  "
                    f"{float(f.get('edge') or 0):>+6.2f}  {f.get('label')}"
                )
            print(
                f"  fold汇总: tp均值={folds.get('tp_given_fill_avg')}  "
                f"std={folds.get('tp_given_fill_std')}  → {folds.get('note')}"
            )
        print(f"  → {item.get('hint')}")

    scans = report.get("parameter_scan") or {}
    for kind, sc in scans.items():
        print()
        card = sc.get("scorecard") or {}
        print(
            f"【参数扫描 {kind}】稳定性={sc.get('stability')}（{card.get('badge_zh')}）  "
            f"tp={sc.get('tp_mean_avg')}±{sc.get('tp_mean_std')}"
        )
        print(f"  {sc.get('note')}")
        if sc.get("heatmap_ascii"):
            print("  —— edge 热力（window × σ）——")
            for line in sc["heatmap_ascii"]:
                print(f"  {line}")
        if sc.get("best"):
            b = sc["best"]
            print(
                f"  较优网格: window={b.get('window')} σ={b.get('width')} "
                f"edge={b.get('edge')} fold={b.get('fold_badge')}"
            )
        if sc.get("worst"):
            wrow = sc["worst"]
            print(
                f"  较差网格: window={wrow.get('window')} σ={wrow.get('width')} "
                f"edge={wrow.get('edge')}"
            )
        if card.get("how_to_read"):
            print(f"  读表: {card.get('how_to_read')}")
    print()
    print("声明: 单票短样本回放，不是未来胜率；引擎=shared_touch。")
