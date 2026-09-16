"""图表底部通俗读图说明（给非技术读者）。"""

from __future__ import annotations

from typing import Any


def caption_for_channel(channel: str, *, has_levels: bool) -> list[str]:
    kinds = {p.strip().lower() for p in channel.replace("+", ",").split(",") if p.strip()}
    lines = [
        "上半图是价格走势；下半图柱子是成交量——柱越高，这根 K 线交易越活跃。",
        "彩色轨道是「价格通道」：默认按因果滚动拟合——每一根只用当时及之前的窗口，"
        "不会把后面主升的斜线提前画进前面的下跌段（不是后视镜）。",
    ]
    if kinds & {"vwreg", "vw", "volreg", "vwlinreg"}:
        lines.append(
            "本图通道按成交量加权：放量那几根对轨道影响更大；黄点表示「价格靠近参考价且放量」。"
        )
    elif kinds & {"reg", "regression", "linreg"}:
        lines.append("回归通道像给近期走势画的「走廊」，中轴是趋势中线，带宽反映波动大小。")
    if kinds & {"donchian", "dc"}:
        lines.append("Donchian 轨取近期最高/最低：适合观察突破（上破）或跌破（下破）。")
    if has_levels:
        lines.append(
            "虚线水平价是可抄到东方财富「条件单」的参考触发价（研究用，工具不会自动下单）。"
        )
    lines.append("若需旧版「整段一次拟合」的回顾通道，可加 --retrospective-channel（仅对比用）。")
    lines.append("横轴只保留交易时段，已去掉午休/隔夜空洞，所以看起来是连续的。")
    return lines


def caption_for_kline(ma: list[int] | None = None) -> list[str]:
    ma = ma or [5, 10, 20]
    ma_txt = "/".join(str(x) for x in ma)
    return [
        "每根蜡烛：实体=开盘到收盘；上下影线=这段时间最高/最低价。涨跌颜色见图例/柱色。",
        f"彩色曲线是均线 MA{ma_txt}：把最近 N 根收盘价平均，用来看趋势方向，不是买卖指令。",
        "一般来说，价格在均线上方且短均线在上，走势偏强；跌到均线下方要更谨慎。",
        "下图成交量帮助确认：上涨放量更有说服力，下跌放量要警惕。",
    ]


def caption_for_daily(ma: list[int] | None = None) -> list[str]:
    ma = ma or [5, 10, 20, 60]
    return [
        "这是日线图：每根蜡烛代表一天，用来看几周到几个月的大方向。",
        f"均线 MA{'/'.join(str(x) for x in ma)}：MA5/10 偏短线，MA20 看波段，MA60 更像中期方向。",
        "价格站在均线上方偏强；若一根大阴线跌破多条均线，常表示趋势转弱，需降低激进。",
        "下方成交量：地量之后放量上涨，往往比无量空涨更值得关注（仍需结合自身风险承受）。",
    ]


def caption_for_intraday() -> list[str]:
    return [
        "这是分时图：只看最近一个交易日，曲线是最新价随时间的变化。",
        "灰色「昨收」线是今天涨跌的参照：价在其上为红盘思路，其下为绿盘思路。",
        "虚线 VWAP 是当天成交量加权均价：价在 VWAP 上方通常偏强，下方偏弱。",
        "下图是分时成交量，午盘前后放量常对应波动加大；本图仅供盘中观察，不是下单指令。",
    ]


def caption_for_width(assessment: dict[str, Any] | None = None) -> list[str]:
    assessment = assessment or {}
    lines = [
        "上半图仍是价格与通道；下半图是「相对宽度%」=(上轨−下轨)/中轴，用来看波动与分歧。",
        "通道变窄（进入绿色窄区）：市场分歧相对小，确定性偏高，条件单轨位更可参考。",
        "通道变宽（进入橙色宽区）：波动/分歧偏大，不确定性高；宜缩小仓位或等宽度收敛再挂激进单。",
        "虚线是本段历史宽度的中位数；确定性分越高表示当前相对越窄（研究用，不是买卖指令）。",
    ]
    if assessment.get("plan_hint"):
        lines.append(f"本次评估：{assessment.get('label')} —— {assessment['plan_hint']}")
    elif assessment.get("label"):
        lines.append(f"本次评估：{assessment.get('label')}（条件单{assessment.get('reasonableness') or '可参考'}）。")
    lines.append("横轴只保留交易时段；工具不下单，价格需人工录入东方财富条件单。")
    return lines


def caption_for_ke(assessment: dict[str, Any] | None = None) -> list[str]:
    assessment = assessment or {}
    lines = [
        "这是物理学隐喻图：质量≈相对成交量，速度≈涨跌幅，动能 KE≈½mv²（研究用，不是物理定律）。",
        "下半图柱高=动能大小；红柱偏上攻推进，绿柱偏下破推进；虚线是本段动能中位数。",
        "动能偏高：有量参与的真推进（突破/续跌叙事更强）；动能耗散：价格易空转，更适合等回踩或收敛。",
        "冲量从高位回落时，常是回踩类条件单更好的窗口；极端单边高动能时不宜抢反方向单。",
    ]
    if assessment.get("plan_hint"):
        lines.append(f"本次评估：{assessment.get('label')} —— {assessment['plan_hint']}")
    elif assessment.get("label"):
        lines.append(
            f"本次评估：{assessment.get('label')}（{assessment.get('reasonableness') or '可参考'}）。"
        )
    lines.append("横轴只保留交易时段；工具不下单，价位需人工录入东方财富条件单。")
    return lines


def apply_caption(fig: Any, lines: list[str], *, title: str = "读图说明") -> None:
    """在图底部画通俗说明；会略微加高画布并留出页脚空间。"""
    if not lines:
        return
    # 关掉 constrained/tight，才能用 subplots_adjust 给页脚腾地方
    try:
        fig.set_layout_engine(None)
    except Exception:
        try:
            fig.set_constrained_layout(False)
        except Exception:
            pass
        try:
            fig.set_tight_layout(False)
        except Exception:
            pass

    n = len(lines)
    w, h = fig.get_size_inches()
    foot = 0.55 + 0.28 * n
    fig.set_size_inches(w, h + foot)
    fig.subplots_adjust(
        left=0.08,
        right=0.98,
        top=0.88,
        bottom=min(0.12 + 0.035 * n, 0.36),
        hspace=0.10,
    )
    body = "\n".join(f"• {line}" for line in lines)
    fig.text(
        0.015,
        0.012,
        f"{title}\n{body}",
        ha="left",
        va="bottom",
        fontsize=8.0,
        linespacing=1.5,
        color="#212529",
        bbox={
            "boxstyle": "round,pad=0.45",
            "facecolor": "#f8f9fa",
            "edgecolor": "#adb5bd",
            "alpha": 0.97,
        },
        zorder=20,
    )
