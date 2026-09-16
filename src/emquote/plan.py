"""条件单方案标准化输出（上班族波段：几天到几周）。

契约化 section（固定目录，缺则显式标明）：
1. conclusion  结论
2. rails       轨位（买/止盈/止损）
3. risk        费用/仓位/涨跌停
4. calibration 历史校准 + 最近决策回放
5. invalidation 失效条件
6. data_boundary 数据边界
"""

from __future__ import annotations

from typing import Any

from .calibrate import calibrate_multi, pick_primary_by_regime
from .energy import compute_kinetic_energy
from .levels import suggest_condition_orders
from .risk import check_limit_constraints, min_stop_gap, net_risk_reward, position_size
from .touch import build_rails, recent_decision_preview


SECTION_ORDER = (
    "conclusion",
    "rails",
    "risk",
    "calibration",
    "invalidation",
    "data_boundary",
)


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
    interval = str(kline.get("interval") or "5m")
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
    kind = str(primary.get("channel") or "vwreg")

    # 轨位与校准回放同源：trailing window + touch.build_rails
    lookback = cal_window if cal_window > 0 else max(_bars_per_day(interval), 96)
    lookback = min(max(lookback, 20), len(bars))
    rails = build_rails(
        bars[-lookback:],
        kind=kind,
        channel_width=channel_width,
        interval=interval,
    )
    buy = float(rails["buy"])
    sell = float(rails["sell"])
    stop = float(rails["stop"])
    buy_low = float(rails["buy_low"])
    buy_high = float(rails["buy_high"])
    mid = float(rails["mid"])
    style = str(rails.get("style") or primary.get("style") or "")
    gap_info = min_stop_gap(last_close, bars)

    rr_info = net_risk_reward(buy, sell, stop)
    validity = _validity_for_interval(interval)
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

    horizon = max(_bars_per_day(interval), int(5 * _bars_per_day(interval)))
    touch_preview = recent_decision_preview(
        bars,
        kind=kind,
        channel_width=channel_width,
        interval=interval,
        lookback=lookback,
        horizon=horizon,
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
    data_notes: list[str] = []
    missing: list[str] = []
    if adjust in {"none", "unknown", ""}:
        data_notes.append(
            "当前 K 线复权标记为 none/未知：跨除权波段轨位可能跳变，研究波段建议 --adjust qfq"
        )
    if limits.get("flags"):
        data_notes.append("存在涨跌停相关成交风险，见 limits.flags")
    if len(bars) < lookback + horizon:
        missing.append("bars_short_for_full_wf")
        data_notes.append("K 线长度偏短，walk-forward / 最近决策回放样本受限")

    how_to = [
        "在东方财富创建条件单：买入触发 ≈ 买入区触发价；止盈/止损分开挂或持仓后补挂",
        f"建议有效期约 {validity['suggested_trading_days']} 个交易日"
        f"（可在 {validity['min_trading_days']}～{validity['max_trading_days']} 日内自行调整）",
        "主图：日线定方向 + 5m 价量通道定触发带；宽度/动能为描述性状态，分时非设单主依据",
        "优先看【历史校准】触达率、fold 成绩单与参数扫描稳定性；几何盈亏比不是期望",
        "仓位按 risk_pct×资金 / 每股风险估算（若提供 --capital）；涨跌停可能导致无法按计划成交",
        "工具只提供研究参考价，需人工录入条件单；不下单、不连接交易",
        "轨位由 emquote.touch.build_rails 生成，与 calibrate 回放同一引擎",
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
    fold_summary = None
    if isinstance(primary_cal, dict) and primary_cal.get("ok"):
        rates = primary_cal.get("rates") or {}
        eb = primary_cal.get("empirical_bayes") or {}
        folds = primary_cal.get("folds") or {}
        cal_summary = {
            "kind": kind,
            "engine": primary_cal.get("engine") or "shared_touch",
            "decisions": primary_cal.get("decisions"),
            "quality": primary_cal.get("quality"),
            "hint": primary_cal.get("hint"),
            "fill_posterior_mean": (rates.get("fill") or {}).get("mean"),
            "tp_given_fill_posterior_mean": (rates.get("tp_given_fill") or {}).get("mean"),
            "tp_given_fill_ci80": (rates.get("tp_given_fill") or {}).get("ci80"),
            "stop_given_fill_posterior_mean": (rates.get("stop_given_fill") or {}).get("mean"),
            "avg_net_r_when_resolved": primary_cal.get("avg_net_r_when_resolved"),
            "empirical_bayes_mode": eb.get("mode"),
            "fold_badge": folds.get("badge"),
            "disclaimer": primary_cal.get("disclaimer"),
        }
        if folds.get("ok"):
            fold_summary = {
                "badge": folds.get("badge"),
                "n_folds": folds.get("n_folds"),
                "tp_given_fill_avg": folds.get("tp_given_fill_avg"),
                "tp_given_fill_std": folds.get("tp_given_fill_std"),
                "note": folds.get("note"),
                "folds": folds.get("folds"),
            }

    scan_summary = None
    if calibration and kind and (calibration.get("parameter_scan") or {}).get(kind):
        sc = calibration["parameter_scan"][kind]
        card = sc.get("scorecard") or {}
        scan_summary = {
            "stability": sc.get("stability"),
            "badge_zh": card.get("badge_zh"),
            "tp_mean_avg": sc.get("tp_mean_avg"),
            "tp_mean_std": sc.get("tp_mean_std"),
            "best": sc.get("best"),
            "worst": sc.get("worst"),
            "heatmap_ascii": sc.get("heatmap_ascii"),
            "note": sc.get("note"),
            "how_to_read": card.get("how_to_read"),
        }

    # —— 契约化 sections ——
    hangable = True
    hang_notes: list[str] = []
    if isinstance(primary_cal, dict) and primary_cal.get("quality") in {"low_sample", "stop_heavy"}:
        hangable = False
        hang_notes.append(f"校准质量={primary_cal.get('quality')}")
    if fold_summary and fold_summary.get("badge") == "fragile":
        hang_notes.append("fold 徽章=fragile")
    if limits.get("flags"):
        hang_notes.append("存在涨跌停约束告警")
    if sizing is not None and not sizing.get("feasible"):
        hangable = False
        hang_notes.append("仓位不可行（买不起1手或止损过近）")

    section_conclusion = {
        "available": True,
        "hangable": hangable,
        "summary": (
            f"主通道 {kind}（{style}），选择来源 {regime_meta.get('source')}；"
            f"{'可考虑挂单参考' if hangable else '建议暂缓/降仓，先看校准与失效条件'}"
        ),
        "primary_channel": kind,
        "primary_style": style,
        "selection": regime_meta,
        "validity_days": validity.get("suggested_trading_days"),
        "notes": hang_notes,
        "kind": "analysis",
    }
    section_rails = {
        "available": True,
        "kind": "derived_fact",
        "engine": "shared_touch",
        "rails_builder": "emquote.touch.build_rails",
        "lookback_bars": lookback,
        "buy_zone": {
            "style": style,
            "low": buy_low,
            "high": buy_high,
            "trigger": buy,
            "eastmoney_hint": (
                "价格大于等于" if kind == "donchian" else "价格小于等于（或区间触达）"
            ),
        },
        "take_profit": {"trigger": sell, "eastmoney_hint": "价格大于等于"},
        "stop_loss": {
            "trigger": stop,
            "eastmoney_hint": "价格小于等于",
            "gap": gap_info,
        },
        "mid": mid,
        "formula_note": "几何轨位由当时末段通道估计；与 calibrate 同一 build_rails。",
    }
    section_risk = {
        "available": True,
        "kind": "derived_fact",
        "risk_reward": {
            "risk_per_share": rr_info["risk_per_share"],
            "reward_per_share": rr_info["reward_per_share"],
            "ratio_gross": rr_info["ratio_gross"],
            "ratio_net": rr_info["ratio_net"],
            "cost_pct_round_trip": rr_info["cost_pct_round_trip"],
            "note": "ratio 为几何盈亏比；ratio_net 为简化费用后，仍非期望 R",
        },
        "position": sizing,
        "limits": limits,
        "missing": [] if sizing is not None else ["position_requires_capital"],
    }
    if sizing is None:
        section_risk["note"] = "未提供 --capital，仓位节缺失（非错误）。"

    section_calibration = {
        "available": bool(cal_summary),
        "kind": "derived_fact",
        "engine": "shared_touch",
        "summary": cal_summary,
        "folds": fold_summary,
        "parameter_stability": scan_summary,
        "recent_decision": touch_preview,
        "missing": [] if cal_summary else (["calibration_skipped"] if not run_calibration else ["calibration_failed"]),
    }
    if not cal_summary:
        section_calibration["note"] = (
            "未跑校准" if not run_calibration else "校准未成功（样本不足或其他）"
        )

    section_invalidation = {
        "available": True,
        "kind": "analysis",
        "items": invalidation,
        "how_to_use": how_to,
    }
    section_data = {
        "available": True,
        "kind": "fact",
        "symbol": report.get("symbol") or kline.get("symbol") or "",
        "interval": interval,
        "adjust": adjust,
        "last_time": report.get("last_time"),
        "last_close": last_close,
        "bars": len(bars),
        "notes": data_notes,
        "missing": missing,
        "width": {
            "rank": width_assessment.get("rank"),
            "label": width_assessment.get("label"),
            "last_width_pct": width_assessment.get("last_width_pct"),
            "plan_hint": width_assessment.get("plan_hint"),
            "score_note": "描述性相对窄/宽得分，不是预测概率",
        },
        "energy": {
            "rank": energy_assess.get("rank"),
            "label": energy_assess.get("label"),
            "plan_hint": energy_assess.get("plan_hint"),
            "note": "描述性隐喻，非 alpha",
        },
    }

    sections = {
        "conclusion": section_conclusion,
        "rails": section_rails,
        "risk": section_risk,
        "calibration": section_calibration,
        "invalidation": section_invalidation,
        "data_boundary": section_data,
    }

    return {
        "contract_version": "plan.v2",
        "section_order": list(SECTION_ORDER),
        "sections": sections,
        # —— 兼容旧字段（CLI / skill / JSON 消费者）——
        "symbol": section_data["symbol"],
        "code": report.get("code") or kline.get("code") or "",
        "name": report.get("name") or kline.get("name") or "",
        "interval": interval,
        "adjust": adjust,
        "last_time": report.get("last_time"),
        "last_close": last_close,
        "audience": "上班族波段（几天到几周），用条件单代替盯盘；不做超短/打板",
        "primary_channel": kind,
        "primary_style": style,
        "primary_label": primary.get("label"),
        "primary_selection": regime_meta,
        "validity": validity,
        "buy_zone": section_rails["buy_zone"],
        "take_profit": section_rails["take_profit"],
        "stop_loss": section_rails["stop_loss"],
        "risk_reward": {
            **section_risk["risk_reward"],
            "ratio": rr_info["ratio_gross"],
            "cost_note": rr_info.get("cost_note"),
            "heuristic": True,
        },
        "position": sizing,
        "limits": limits,
        "width": {
            **section_data["width"],
            "certainty_score": width_assessment.get("certainty_score"),
            "mean_width_pct": width_assessment.get("mean_width_pct"),
            "reasonableness": width_assessment.get("reasonableness"),
            "channels": width_assessment.get("channels") or [],
        },
        "energy": {
            **section_data["energy"],
            "reasonableness": energy_assess.get("reasonableness"),
            "operability_score": energy_assess.get("operability_score"),
            "last_ke": energy_pack.get("last_ke"),
            "last_mass": energy_pack.get("last_mass"),
            "last_velocity": energy_pack.get("last_velocity"),
            "decaying": energy_assess.get("decaying"),
            "note": energy_assess.get("note"),
            "causal": energy_assess.get("causal"),
        },
        "calibration": cal_summary,
        "fold_scorecard": fold_summary,
        "parameter_stability": scan_summary,
        "touch_preview": touch_preview,
        "calibration_detail": calibration,
        "data_notes": data_notes,
        "invalidation": invalidation,
        "how_to_use": how_to,
        "charts_recommended": ["daily", "kline", "pv", "width", "ke"],
        "charts_optional": [],
        "levels_report": report,
        "engine": "shared_touch",
        "disclaimer": (
            "研究参考而非投资建议；几何轨位 + 描述性状态 + 单票时序回放。"
            "人工录入条件单；不下单、不保证触达或收益。"
            "事实/派生/分析见 sections.*.kind。"
        ),
    }


def print_condition_plan(plan: dict[str, Any]) -> None:
    sections = plan.get("sections") or {}
    print("东方财富条件单方案（契约 plan.v2 · 共享触达引擎 · 研究用 · 不下单）")
    print(
        f"标的: {plan.get('name')} {plan.get('symbol')}  "
        f"周期: {plan.get('interval')}  复权: {plan.get('adjust')}  "
        f"最新: {plan.get('last_close')}  时间: {plan.get('last_time')}"
    )
    print(f"定位: {plan.get('audience')}")

    # 1 结论
    conc = sections.get("conclusion") or {}
    print()
    print("【1. 结论】")
    print(f"  {conc.get('summary')}")
    if conc.get("notes"):
        for n in conc["notes"]:
            print(f"  · {n}")
    v = plan.get("validity") or {}
    print(
        f"  建议有效期: {v.get('suggested_trading_days')} 个交易日"
        f"（范围 {v.get('min_trading_days')}～{v.get('max_trading_days')}）"
    )
    if v.get("calibration_note"):
        print(f"  校准附注: {v.get('calibration_note')}")

    # 2 轨位
    rails = sections.get("rails") or {}
    bz = (rails.get("buy_zone") or plan.get("buy_zone") or {})
    tp = (rails.get("take_profit") or plan.get("take_profit") or {})
    sl = (rails.get("stop_loss") or plan.get("stop_loss") or {})
    print()
    print("【2. 轨位】（派生事实 · shared_touch）")
    print(
        f"  买入区: {bz.get('low')} ～ {bz.get('high')}  "
        f"（触发参考 {bz.get('trigger')}，{bz.get('eastmoney_hint')}）"
    )
    print(f"  止盈价: {tp.get('trigger')}  （{tp.get('eastmoney_hint')}）")
    print(f"  止损价: {sl.get('trigger')}  （{sl.get('eastmoney_hint')}）")
    gap = sl.get("gap") or {}
    if gap:
        print(
            f"  止损间距规则: {gap.get('method')}  "
            f"ATR={gap.get('atr')} → gap={gap.get('gap')}"
        )

    # 3 风控
    risk = sections.get("risk") or {}
    rr = risk.get("risk_reward") or plan.get("risk_reward") or {}
    print()
    print("【3. 风控 / 仓位 / 涨跌停】")
    if rr.get("ratio_gross") is not None:
        print(
            f"  几何盈亏比: {rr.get('ratio_gross')}  |  简化费用后: {rr.get('ratio_net')}  "
            f"（成本率≈{rr.get('cost_pct_round_trip')}）"
        )
    pos = risk.get("position") if "position" in risk else plan.get("position")
    if pos:
        print(
            f"  资金 {pos.get('capital')} × 风险 {float(pos.get('risk_pct') or 0)*100:.2f}% "
            f"→ 约 {pos.get('shares')} 股（{pos.get('lots')} 手）"
        )
        if not pos.get("feasible"):
            print("  ⚠ 买不起 1 手或止损过近")
    elif risk.get("missing"):
        print(f"  仓位节缺失: {', '.join(risk.get('missing') or [])}（传 --capital 可补）")
    lim = risk.get("limits") or plan.get("limits") or {}
    if lim:
        print(
            f"  板块 {lim.get('board')} ±{float(lim.get('limit_pct') or 0)*100:.0f}%  "
            f"涨停 {lim.get('limit_up')} / 跌停 {lim.get('limit_down')}"
        )
        for f in lim.get("flags") or []:
            print(f"  ⚠ {f}")

    # 4 校准
    print()
    print("【4. 历史校准】")
    cal_sec = sections.get("calibration") or {}
    cal = cal_sec.get("summary") or plan.get("calibration") or {}
    if not cal_sec.get("available"):
        print(f"  （本节无数据）{cal_sec.get('note') or cal_sec.get('missing')}")
    else:
        print(
            f"  决策点: {cal.get('decisions')}  质量: {cal.get('quality')}  "
            f"fold徽章: {cal.get('fold_badge')}  EB: {cal.get('empirical_bayes_mode')}"
        )
        print(
            f"  成交后验: {cal.get('fill_posterior_mean')}  "
            f"先止盈|成交: {cal.get('tp_given_fill_posterior_mean')}  "
            f"CI80={cal.get('tp_given_fill_ci80')}  "
            f"先止损|成交: {cal.get('stop_given_fill_posterior_mean')}"
        )
        if cal.get("hint"):
            print(f"  提示: {cal.get('hint')}")
        folds = cal_sec.get("folds") or plan.get("fold_scorecard") or {}
        if folds and folds.get("folds"):
            print(
                f"  fold: badge={folds.get('badge')}  "
                f"tp均值={folds.get('tp_given_fill_avg')}±{folds.get('tp_given_fill_std')}"
            )
            print(
                f"  {'fold':>4}  {'n':>4}  {'tp|fill':>7}  {'edge':>6}  label"
            )
            for f in folds["folds"]:
                print(
                    f"  {f.get('fold'):>4}  {f.get('decisions'):>4}  "
                    f"{float(f.get('tp_given_fill') or 0):>7.2f}  "
                    f"{float(f.get('edge') or 0):>+6.2f}  {f.get('label')}"
                )
        preview = cal_sec.get("recent_decision") or plan.get("touch_preview") or {}
        if preview.get("ok"):
            print(
                f"  最近决策回放: outcome={preview.get('outcome')}  "
                f"（与 calibrate 同尺，非预测）"
            )
        stab = cal_sec.get("parameter_stability") or plan.get("parameter_stability") or {}
        if stab:
            print(
                f"  参数扫描: {stab.get('stability')}（{stab.get('badge_zh')}）  "
                f"tp={stab.get('tp_mean_avg')}±{stab.get('tp_mean_std')}"
            )
            for line in stab.get("heatmap_ascii") or []:
                print(f"    {line}")

    # 5 失效
    print()
    print("【5. 失效条件】")
    for item in (sections.get("invalidation") or {}).get("items") or plan.get("invalidation") or []:
        print(f"  - {item}")

    # 6 数据边界
    print()
    print("【6. 数据边界】")
    data = sections.get("data_boundary") or {}
    print(
        f"  bars={data.get('bars')}  adjust={data.get('adjust')}  "
        f"width={((data.get('width') or {}).get('label'))}  "
        f"energy={((data.get('energy') or {}).get('label'))}"
    )
    for note in data.get("notes") or plan.get("data_notes") or []:
        print(f"  · {note}")
    if data.get("missing"):
        print(f"  missing: {', '.join(data['missing'])}")

    print()
    print("【使用提示】")
    for item in (sections.get("invalidation") or {}).get("how_to_use") or plan.get("how_to_use") or []:
        print(f"  - {item}")
    print()
    print(f"说明: {plan.get('disclaimer')}")
