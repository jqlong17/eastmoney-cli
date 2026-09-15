"""条件单方案标准化输出（上班族波段：几天到几周）。"""

from __future__ import annotations

from typing import Any

from .calibrate import calibrate_multi, pick_primary_by_regime
from .energy import compute_kinetic_energy
from .levels import round_price, suggest_condition_orders
from .risk import check_limit_constraints, min_stop_gap, net_risk_reward, position_size


def _validity_for_interval(interval: str) -> dict[str, Any]:
    key = (interval or "5m").lower()
    if key in {"1d", "day"}:
        return {
            "min_trading_days": 5,
            "max_trading_days": 15,
            "suggested_trading_days": 10,
            "note": "日线结构偏慢，建议条件单挂 5～15 个交易日，到期未触发再复盘。",
            "heuristic": True,
        }
    return {
        "min_trading_days": 3,
        "max_trading_days": 10,
        "suggested_trading_days": 5,
        "note": "服务几天到几周的波段；默认建议约 5 个交易日（启发式，可用校准 fill 率对照）。",
        "heuristic": True,
    }


def build_condition_plan(
    kline: dict[str, Any],
    *,
    channels: list[str] | None = None,
    channel_window: int = 0,
    channel_width: float = 2.0,
    full_range: bool = True,
    run_calibration: bool = True,
    with_param_scan: bool = False,
    capital: float | None = None,
    risk_pct: float = 0.01,
) -> dict[str, Any]:
    """生成可抄进东财的波段条件单方案（研究用，不下单）。"""
    channels = channels or ["vwreg", "donchian"]
    bars = kline.get("bars") or []
    report = suggest_condition_orders(
        kline,
        channels=channels,
        channel_window=channel_window,
        channel_width=channel_width,
        full_range=full_range,
    )
    width_assessment = report.get("width_assessment") or {}
    energy_pack = compute_kinetic_energy(bars)
    energy_assess = energy_pack.get("assessment") or {}

    cal_window = channel_window if channel_window > 0 else 96
    calibration = None
    if run_calibration:
        calibration = calibrate_multi(
            kline,
            kinds=[c for c in channels if c != "none"],
            channel_window=cal_window,
            channel_width=channel_width,
            validity_days=5,
            with_scan=with_param_scan,
        )

    primary, regime_meta = pick_primary_by_regime(
        report.get("suggestions") or [],
        width_rank=width_assessment.get("rank"),
        energy_rank=energy_assess.get("rank"),
        calibration=calibration,
    )

    last_close = float(report["last_close"])
    buy = float(primary["buy_price"])
    sell = float(primary["sell_price"])
    stop = float(primary["stop_price"])
    mid = float(primary.get("last_mid") or last_close)
    lower = float(primary.get("last_lower") or stop)
    upper = float(primary.get("last_upper") or sell)
    kind = primary.get("channel")

    gap_info = min_stop_gap(last_close, bars)
    min_gap = float(gap_info["gap"])

    if kind == "donchian":
        buy = round_price(upper + max(round_price(last_close * 0.002), 0.01))
        stop = round_price(mid)
        half = max(abs(upper - mid), min_gap)
        sell = round_price(buy + half)
        buy_low = buy
        buy_high = round_price(buy * 1.005)
        style = "突破确认（多头）"
    else:
        band = max(round_price(abs(mid - buy) * 0.25), 0.02)
        buy_low = round_price(max(stop + 0.01, buy - band))
        buy_high = round_price(buy + band)
        style = "回踩买入区"
        if round_price(buy_low - stop) < min_gap:
            stop = round_price(buy_low - min_gap)

    if buy - stop <= 0:
        stop = round_price(buy - min_gap)
    if sell <= buy:
        sell = round_price(buy + max(min_gap, abs(mid - lower), 0.05))

    rr_info = net_risk_reward(buy, sell, stop)
    validity = _validity_for_interval(str(kline.get("interval") or report.get("interval") or "5m"))

    validity = {
        **validity,
        "width_note": width_assessment.get("plan_hint") or "",
        "calibration_note": "",
    }
    primary_cal = None
    if calibration and kind:
        primary_cal = (calibration.get("by_kind") or {}).get(kind)
    if isinstance(primary_cal, dict) and primary_cal.get("ok"):
        expire_share = (primary_cal.get("rates") or {}).get("expire_unfilled_share")
        if isinstance(expire_share, (int, float)) and expire_share > 0.55:
            validity["suggested_trading_days"] = min(
                int(validity["max_trading_days"]),
                int(validity["suggested_trading_days"]) + 2,
            )
            validity["calibration_note"] = (
                "回放中未成交比例偏高，启发式略放长有效期；仍非最优参数。"
            )
        elif isinstance(expire_share, (int, float)) and expire_share < 0.25:
            validity["calibration_note"] = "回放中成交相对容易；有效期可维持默认。"

    # 涨跌停参考：用前收（倒数第二根收盘）近似
    ref_close = float(bars[-2]["close"]) if len(bars) >= 2 else last_close
    limits = check_limit_constraints(
        buy=buy,
        sell=sell,
        stop=stop,
        ref_close=ref_close,
        symbol=str(report.get("symbol") or kline.get("symbol") or ""),
        name=str(report.get("name") or kline.get("name") or ""),
    )

    sizing = None
    if capital is not None and capital > 0:
        sizing = position_size(
            capital=float(capital),
            risk_pct=float(risk_pct),
            buy=buy,
            stop=stop,
        )

    invalidation = [
        f"收盘价跌破止损参考价 {stop:.2f}（计划失效，不再等待买入/持有）",
        f"有效期内价格从未进入买入区 {buy_low:.2f}～{buy_high:.2f}，到期后需重新评估",
        "日线关键均线（如 MA20/MA60）明显转空且放量跌破，波段前提被破坏",
    ]
    if kind == "donchian":
        invalidation.append(f"突破后迅速回到中轴附近 {mid:.2f}，视为假突破")
    for flag in limits.get("flags") or []:
        invalidation.append(f"涨跌停约束：{flag}")

    adjust = str(kline.get("adjust") or "unknown")
    data_notes = []
    if adjust in {"none", "unknown", ""}:
        data_notes.append(
            "当前 K 线复权标记为 none/未知：跨除权波段轨位可能跳变，研究波段建议 --adjust qfq"
        )
    if limits.get("flags"):
        data_notes.append("存在涨跌停相关成交风险，见 limits.flags")

    how_to = [
        "在东方财富创建条件单：买入触发 ≈ 买入区触发价；止盈/止损分开挂或持仓后补挂",
        f"建议有效期约 {validity['suggested_trading_days']} 个交易日"
        f"（可在 {validity['min_trading_days']}～{validity['max_trading_days']} 日内自行调整）",
        "主图：日线定方向 + 5m 价量通道定触发带；宽度/动能为描述性状态，分时非设单主依据",
        "优先看【历史校准】触达率与参数扫描稳定性；几何盈亏比不是期望",
        "仓位按 risk_pct×资金 / 每股风险估算（若提供 --capital）；涨跌停可能导致无法按计划成交",
        "工具只提供研究参考价，需人工录入条件单；不下单、不连接交易",
    ]
    if width_assessment.get("plan_hint"):
        how_to.insert(3, f"宽度（描述性）：{width_assessment['plan_hint']}")
    if energy_assess.get("plan_hint"):
        how_to.insert(4, f"动能（描述性）：{energy_assess['plan_hint']}")
    if isinstance(primary_cal, dict) and primary_cal.get("hint"):
        how_to.insert(3, f"校准：{primary_cal['hint']}")
    for note in data_notes:
        how_to.append(note)

    cal_summary = None
    if isinstance(primary_cal, dict) and primary_cal.get("ok"):
        rates = primary_cal.get("rates") or {}
        eb = primary_cal.get("empirical_bayes") or {}
        cal_summary = {
            "kind": kind,
            "decisions": primary_cal.get("decisions"),
            "quality": primary_cal.get("quality"),
            "hint": primary_cal.get("hint"),
            "fill_posterior_mean": (rates.get("fill") or {}).get("mean"),
            "tp_given_fill_posterior_mean": (rates.get("tp_given_fill") or {}).get("mean"),
            "tp_given_fill_ci80": (rates.get("tp_given_fill") or {}).get("ci80"),
            "stop_given_fill_posterior_mean": (rates.get("stop_given_fill") or {}).get("mean"),
            "avg_net_r_when_resolved": primary_cal.get("avg_net_r_when_resolved"),
            "empirical_bayes_mode": eb.get("mode"),
            "disclaimer": primary_cal.get("disclaimer"),
        }

    scan_summary = None
    if calibration and kind and (calibration.get("parameter_scan") or {}).get(kind):
        sc = calibration["parameter_scan"][kind]
        scan_summary = {
            "stability": sc.get("stability"),
            "tp_mean_avg": sc.get("tp_mean_avg"),
            "tp_mean_std": sc.get("tp_mean_std"),
            "best": sc.get("best"),
            "note": sc.get("note"),
        }

    return {
        "symbol": report.get("symbol") or kline.get("symbol") or "",
        "code": report.get("code") or kline.get("code") or "",
        "name": report.get("name") or kline.get("name") or "",
        "interval": report.get("interval") or kline.get("interval") or "",
        "adjust": adjust,
        "last_time": report.get("last_time"),
        "last_close": last_close,
        "audience": "上班族波段（几天到几周），用条件单代替盯盘；不做超短/打板",
        "primary_channel": kind,
        "primary_style": primary.get("style"),
        "primary_label": primary.get("label"),
        "primary_selection": regime_meta,
        "validity": validity,
        "buy_zone": {
            "style": style,
            "low": buy_low,
            "high": buy_high,
            "trigger": buy,
            "eastmoney_hint": (
                "价格大于等于" if kind == "donchian" else "价格小于等于（或区间触达）"
            ),
            "说明": primary.get("buy", {}).get("说明") or primary.get("buy", {}).get("用途"),
        },
        "take_profit": {
            "trigger": sell,
            "eastmoney_hint": "价格大于等于",
            "说明": primary.get("sell", {}).get("说明") or primary.get("sell", {}).get("用途"),
        },
        "stop_loss": {
            "trigger": stop,
            "eastmoney_hint": "价格小于等于",
            "说明": primary.get("stop", {}).get("说明") or primary.get("stop", {}).get("用途"),
            "gap": gap_info,
        },
        "risk_reward": {
            "risk_per_share": rr_info["risk_per_share"],
            "reward_per_share": rr_info["reward_per_share"],
            "ratio": rr_info["ratio_gross"],
            "ratio_gross": rr_info["ratio_gross"],
            "ratio_net": rr_info["ratio_net"],
            "cost_pct_round_trip": rr_info["cost_pct_round_trip"],
            "cost_note": rr_info["cost_note"],
            "heuristic": True,
            "note": "ratio 为几何盈亏比；ratio_net 为简化费用后，仍非期望 R",
        },
        "position": sizing,
        "limits": limits,
        "width": {
            "rank": width_assessment.get("rank"),
            "label": width_assessment.get("label"),
            "certainty_score": width_assessment.get("certainty_score"),
            "score_note": "描述性相对窄/宽得分，不是预测概率",
            "last_width_pct": width_assessment.get("last_width_pct"),
            "mean_width_pct": width_assessment.get("mean_width_pct"),
            "reasonableness": width_assessment.get("reasonableness"),
            "plan_hint": width_assessment.get("plan_hint"),
            "channels": width_assessment.get("channels") or [],
        },
        "energy": {
            "rank": energy_assess.get("rank"),
            "label": energy_assess.get("label"),
            "reasonableness": energy_assess.get("reasonableness"),
            "operability_score": energy_assess.get("operability_score"),
            "plan_hint": energy_assess.get("plan_hint"),
            "last_ke": energy_pack.get("last_ke"),
            "last_mass": energy_pack.get("last_mass"),
            "last_velocity": energy_pack.get("last_velocity"),
            "decaying": energy_assess.get("decaying"),
            "note": energy_assess.get("note"),
            "causal": energy_assess.get("causal"),
        },
        "calibration": cal_summary,
        "parameter_stability": scan_summary,
        "calibration_detail": calibration,
        "data_notes": data_notes,
        "invalidation": invalidation,
        "how_to_use": how_to,
        "charts_recommended": ["daily", "kline", "pv", "width", "ke"],
        "charts_optional": [],
        "levels_report": report,
        "disclaimer": (
            "研究参考而非投资建议；几何轨位 + 描述性状态 + 单票时序回放。"
            "人工录入条件单；不下单、不保证触达或收益。"
        ),
    }


def print_condition_plan(plan: dict[str, Any]) -> None:
    print("东方财富条件单方案（上班族波段 · 研究用 · 不下单）")
    print(
        f"标的: {plan.get('name')} {plan.get('symbol')}  "
        f"周期: {plan.get('interval')}  复权: {plan.get('adjust')}  "
        f"最新: {plan.get('last_close')}  时间: {plan.get('last_time')}"
    )
    print(f"定位: {plan.get('audience')}")
    sel = plan.get("primary_selection") or {}
    print(
        f"主通道: {plan.get('primary_style')} · {plan.get('primary_label')} "
        f"({plan.get('primary_channel')})  "
        f"[选择: {sel.get('source')}]"
    )
    v = plan.get("validity") or {}
    print(
        f"建议有效期: {v.get('suggested_trading_days')} 个交易日"
        f"（范围 {v.get('min_trading_days')}～{v.get('max_trading_days')}）；{v.get('note')}"
    )
    if v.get("calibration_note"):
        print(f"  校准附注: {v.get('calibration_note')}")
    bz = plan.get("buy_zone") or {}
    tp = plan.get("take_profit") or {}
    sl = plan.get("stop_loss") or {}
    print()
    print("【可抄条件单】")
    print(
        f"  买入区: {bz.get('low')} ～ {bz.get('high')}  "
        f"（触发参考 {bz.get('trigger')}，{bz.get('eastmoney_hint')}）"
    )
    print(f"  止盈价: {tp.get('trigger')}  （{tp.get('eastmoney_hint')}）")
    print(f"  止损价: {sl.get('trigger')}  （{sl.get('eastmoney_hint')}）")
    gap = (sl.get("gap") or {})
    if gap:
        print(
            f"  止损间距规则: {gap.get('method')}  "
            f"ATR={gap.get('atr')} → gap={gap.get('gap')}"
        )
    rr = plan.get("risk_reward") or {}
    if rr.get("ratio") is not None:
        print(
            f"  几何盈亏比: {rr.get('ratio_gross')}  |  简化费用后: {rr.get('ratio_net')}  "
            f"（成本率≈{rr.get('cost_pct_round_trip')}；{rr.get('note')}）"
        )
    pos = plan.get("position")
    if pos:
        print()
        print("【仓位预算（研究用）】")
        print(
            f"  资金 {pos.get('capital')} × 风险 {float(pos.get('risk_pct') or 0)*100:.2f}% "
            f"→ 预算 {pos.get('risk_budget')}  → 约 {pos.get('shares')} 股 "
            f"（{pos.get('lots')} 手）名义 {pos.get('notional')}  "
            f"止损最大亏约 {pos.get('max_loss_if_stop')}"
        )
        if not pos.get("feasible"):
            print("  ⚠ 买不起 1 手或止损过近，请提高资金或放宽 risk_pct")
        print(f"  注: {pos.get('note')}")
    lim = plan.get("limits") or {}
    if lim:
        print()
        print("【涨跌停约束（粗估）】")
        print(
            f"  板块 {lim.get('board')} ±{float(lim.get('limit_pct') or 0)*100:.0f}%  "
            f"参考价 {lim.get('ref_close')}  "
            f"涨停 {lim.get('limit_up')} / 跌停 {lim.get('limit_down')}"
        )
        if lim.get("flags"):
            for f in lim["flags"]:
                print(f"  ⚠ {f}")
        else:
            print("  当前买/卖/止损未明显贴板")
    cal = plan.get("calibration") or {}
    if cal:
        print()
        print("【历史校准 walk-forward · 时序经验贝叶斯】")
        print(
            f"  决策点: {cal.get('decisions')}  质量: {cal.get('quality')}  "
            f"EB模式: {cal.get('empirical_bayes_mode')}  "
            f"成交后验均值: {cal.get('fill_posterior_mean')}"
        )
        print(
            f"  成交后先触止盈后验: {cal.get('tp_given_fill_posterior_mean')}  "
            f"CI80={cal.get('tp_given_fill_ci80')}  "
            f"先触止损后验: {cal.get('stop_given_fill_posterior_mean')}"
        )
        if cal.get("avg_net_r_when_resolved") is not None:
            print(f"  已分晓交易的平均净 R: {cal.get('avg_net_r_when_resolved')}")
        if cal.get("hint"):
            print(f"  提示: {cal.get('hint')}")
        if cal.get("disclaimer"):
            print(f"  声明: {cal.get('disclaimer')}")
    stab = plan.get("parameter_stability") or {}
    if stab:
        print()
        print("【参数稳定性扫描】")
        print(
            f"  稳定性: {stab.get('stability')}  "
            f"tp均值 {stab.get('tp_mean_avg')} ± {stab.get('tp_mean_std')}"
        )
        if stab.get("best"):
            b = stab["best"]
            print(
                f"  网格较优: window={b.get('window')} σ={b.get('width')}  "
                f"edge={b.get('edge')} tp={b.get('tp_mean')}"
            )
        if stab.get("note"):
            print(f"  注: {stab.get('note')}")
    w = plan.get("width") or {}
    if w:
        print()
        print("【通道宽度（描述性）】")
        print(
            f"  状态: {w.get('label')}  {w.get('reasonableness')}  "
            f"相对分: {w.get('certainty_score')}  宽度: {w.get('last_width_pct')}%"
        )
        if w.get("score_note"):
            print(f"  注: {w.get('score_note')}")
    e = plan.get("energy") or {}
    if e:
        print()
        print("【价量动能（描述性隐喻）】")
        print(
            f"  状态: {e.get('label')}  {e.get('reasonableness')}  "
            f"KE: {e.get('last_ke')}"
        )
    for note in plan.get("data_notes") or []:
        print()
        print(f"【数据注意】{note}")
    print()
    print("【失效条件】")
    for item in plan.get("invalidation") or []:
        print(f"  - {item}")
    print()
    print("【使用提示】")
    for item in plan.get("how_to_use") or []:
        print(f"  - {item}")
    print()
    print(f"说明: {plan.get('disclaimer')}")
