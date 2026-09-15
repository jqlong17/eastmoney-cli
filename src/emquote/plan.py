"""条件单方案标准化输出（上班族波段：几天到几周）。"""

from __future__ import annotations

from typing import Any

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
        "invalidation": invalidation,
        "how_to_use": [
            "在东方财富创建条件单：买入触发 ≈ 买入区触发价；止盈/止损分开挂或持仓后补挂",
            f"建议有效期约 {validity['suggested_trading_days']} 个交易日"
            f"（可在 {validity['min_trading_days']}～{validity['max_trading_days']} 日内自行调整）",
            "主图看日线定方向 + 5 分钟价量通道定触发带；分时仅作辅图，不作为设单主依据",
            "工具只提供研究参考价，需人工录入条件单；不下单、不连接交易",
        ],
        "charts_recommended": ["daily", "kline", "pv"],
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
