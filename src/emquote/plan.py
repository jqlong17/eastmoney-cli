"""条件单方案标准化输出（上班族波段：几天到几周）。"""

from __future__ import annotations

from typing import Any

from .energy import compute_kinetic_energy
from .levels import round_price, suggest_condition_orders


def _validity_for_interval(interval: str) -> dict[str, Any]:
    key = (interval or "5m").lower()
    if key in {"1d", "day"}:
        return {
            "min_trading_days": 5,
            "max_trading_days": 15,
            "suggested_trading_days": 10,
            "note": "日线结构偏慢，建议条件单挂 5～15 个交易日，到期未触发再复盘。",
        }
    return {
        "min_trading_days": 3,
        "max_trading_days": 10,
        "suggested_trading_days": 5,
        "note": "服务几天到几周的波段；建议条件单有效期约 3～10 个交易日（默认建议 5 日）。",
    }


def _pick_primary(suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    if not suggestions:
        raise ValueError("无条件单建议")
    for pref in ("vwreg", "reg", "donchian", "hl"):
        for item in suggestions:
            if item.get("channel") == pref:
                return item
    return suggestions[0]


def build_condition_plan(
    kline: dict[str, Any],
    *,
    channels: list[str] | None = None,
    channel_window: int = 0,
    channel_width: float = 2.0,
    full_range: bool = True,
) -> dict[str, Any]:
    """生成可抄进东财的波段条件单方案（研究用，不下单）。"""
    channels = channels or ["vwreg", "donchian"]
    report = suggest_condition_orders(
        kline,
        channels=channels,
        channel_window=channel_window,
        channel_width=channel_width,
        full_range=full_range,
    )
    primary = _pick_primary(report.get("suggestions") or [])
    last_close = float(report["last_close"])
    buy = float(primary["buy_price"])
    sell = float(primary["sell_price"])
    stop = float(primary["stop_price"])
    mid = float(primary.get("last_mid") or last_close)

    # 买入区：均线回归类用下轨附近带宽；突破类用触发价上方窄带
    kind = primary.get("channel")
    if kind == "donchian":
        buy_low = buy
        buy_high = round_price(buy * 1.005)
        style = "突破回踩/站上确认"
    else:
        band = max(round_price(abs(mid - buy) * 0.25), 0.02)
        buy_low = round_price(max(stop + 0.01, buy - band))
        buy_high = round_price(buy + band)
        style = "回踩买入区"

    # 上班族波段：止损需与买入区有可执行间距（默认至少约 0.8%）
    min_gap = max(round_price(last_close * 0.008), 0.05)
    if round_price(buy_low - stop) < min_gap:
        stop = round_price(buy_low - min_gap)
    risk = round_price(buy - stop)
    if risk <= 0:
        risk = min_gap
        stop = round_price(buy - risk)
    reward = round_price(sell - buy)
    rr = round(reward / risk, 2) if risk > 0 else None
    validity = _validity_for_interval(str(kline.get("interval") or report.get("interval") or "5m"))

    invalidation = [
        f"收盘价跌破止损参考价 {stop:.2f}（计划失效，不再等待买入/持有）",
        f"有效期内价格从未进入买入区 {buy_low:.2f}～{buy_high:.2f}，到期后需重新评估",
        "日线关键均线（如 MA20/MA60）明显转空且放量跌破，波段前提被破坏",
    ]
    if kind == "donchian":
        invalidation.append(f"突破后迅速回到中轴附近 {mid:.2f}，视为假突破")

    width_assessment = report.get("width_assessment") or {}
    width_rank = width_assessment.get("rank")
    # 宽度影响有效期建议：偏宽时略放长观察窗口，偏窄时可偏短
    if width_rank == "wide":
        validity = {
            **validity,
            "suggested_trading_days": min(
                int(validity["max_trading_days"]),
                int(validity["suggested_trading_days"]) + 2,
            ),
            "width_note": "通道偏宽：建议略放长有效期，或等宽度收敛后再激进挂单。",
        }
    elif width_rank == "narrow":
        validity = {
            **validity,
            "suggested_trading_days": max(
                int(validity["min_trading_days"]),
                int(validity["suggested_trading_days"]) - 1,
            ),
            "width_note": "通道偏窄：轨位更清晰，有效期可略短，触发后更应及时复盘。",
        }
    else:
        validity = {**validity, "width_note": width_assessment.get("plan_hint") or ""}

    how_to = [
        "在东方财富创建条件单：买入触发 ≈ 买入区触发价；止盈/止损分开挂或持仓后补挂",
        f"建议有效期约 {validity['suggested_trading_days']} 个交易日"
        f"（可在 {validity['min_trading_days']}～{validity['max_trading_days']} 日内自行调整）",
        "主图看日线定方向 + 5 分钟价量通道定触发带；分时仅作辅图，不作为设单主依据",
        "通道宽度反映分歧/不确定性：偏窄更利于设单，偏宽宜谨慎或等收敛",
        "工具只提供研究参考价，需人工录入条件单；不下单、不连接交易",
    ]
    if width_assessment.get("plan_hint"):
        how_to.insert(3, f"宽度评估：{width_assessment['plan_hint']}")

    energy_pack = compute_kinetic_energy(kline.get("bars") or [])
    energy_assess = energy_pack.get("assessment") or {}
    if energy_assess.get("plan_hint"):
        how_to.insert(4, f"动能评估：{energy_assess['plan_hint']}")

    return {
        "symbol": report.get("symbol") or kline.get("symbol") or "",
        "code": report.get("code") or kline.get("code") or "",
        "name": report.get("name") or kline.get("name") or "",
        "interval": report.get("interval") or kline.get("interval") or "",
        "last_time": report.get("last_time"),
        "last_close": last_close,
        "audience": "上班族波段（几天到几周），用条件单代替盯盘；不做超短/打板",
        "primary_channel": kind,
        "primary_style": primary.get("style"),
        "primary_label": primary.get("label"),
        "validity": validity,
        "buy_zone": {
            "style": style,
            "low": buy_low,
            "high": buy_high,
            "trigger": buy,
            "eastmoney_hint": "价格小于等于（或区间触达）",
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
        },
        "risk_reward": {
            "risk_per_share": risk,
            "reward_per_share": reward,
            "ratio": rr,
        },
        "width": {
            "rank": width_assessment.get("rank"),
            "label": width_assessment.get("label"),
            "certainty_score": width_assessment.get("certainty_score"),
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
        },
        "invalidation": invalidation,
        "how_to_use": how_to,
        "charts_recommended": ["daily", "kline", "pv", "width", "ke"],
        "charts_optional": ["intraday"],
        "levels_report": report,
        "disclaimer": report.get("disclaimer")
        or "仅为基于公开行情的研究参考，不是投资建议；请人工录入条件单。",
    }


def print_condition_plan(plan: dict[str, Any]) -> None:
    print("东方财富条件单方案（上班族波段 · 研究用 · 不下单）")
    print(
        f"标的: {plan.get('name')} {plan.get('symbol')}  "
        f"周期: {plan.get('interval')}  最新: {plan.get('last_close')}  "
        f"时间: {plan.get('last_time')}"
    )
    print(f"定位: {plan.get('audience')}")
    print(
        f"主通道: {plan.get('primary_style')} · {plan.get('primary_label')} "
        f"({plan.get('primary_channel')})"
    )
    v = plan.get("validity") or {}
    print(
        f"建议有效期: {v.get('suggested_trading_days')} 个交易日"
        f"（范围 {v.get('min_trading_days')}～{v.get('max_trading_days')}）；{v.get('note')}"
    )
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
    rr = plan.get("risk_reward") or {}
    if rr.get("ratio") is not None:
        print(
            f"  约略盈亏比: {rr.get('ratio')}  "
            f"（每单位风险 {rr.get('risk_per_share')} / 空间 {rr.get('reward_per_share')}）"
        )
    w = plan.get("width") or {}
    if w:
        print()
        print("【通道宽度 / 确定性】")
        print(
            f"  状态: {w.get('label')}  合理性: {w.get('reasonableness')}  "
            f"确定性分: {w.get('certainty_score')}  "
            f"当前相对宽度: {w.get('last_width_pct')}%"
        )
        if w.get("plan_hint"):
            print(f"  提示: {w.get('plan_hint')}")
    e = plan.get("energy") or {}
    if e:
        print()
        print("【价量动能 ½mv²】")
        print(
            f"  状态: {e.get('label')}  合理性: {e.get('reasonableness')}  "
            f"可操作性: {e.get('operability_score')}  "
            f"KE: {e.get('last_ke')}  m={e.get('last_mass')}  "
            f"v={float(e.get('last_velocity') or 0)*100:.3f}%"
        )
        if e.get("plan_hint"):
            print(f"  提示: {e.get('plan_hint')}")
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
