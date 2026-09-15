"""把 K 线画成「上价格 / 下成交量」图（PNG）。

分钟级数据按「交易 bar 序号」横轴绘制，跳过隔夜/周末等非交易时段，
避免休市空洞被连成斜线。

短线可用线性回归通道（三条直线）或 Donchian 通道辅助观察。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _setup_chinese_font() -> None:
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    candidates = [
        "PingFang SC",
        "Heiti SC",
        "STHeiti",
        "Songti SC",
        "Arial Unicode MS",
        "Noto Sans CJK SC",
        "SimHei",
    ]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def _label_for_bar(time_text: str, *, intraday: bool) -> str:
    """横轴标签：分钟线标到时刻，日线只标日期。"""
    if intraday and " " in time_text:
        date, hm = time_text.split(" ", 1)
        return f"{date[5:]} {hm}"  # MM-DD HH:MM
    if " " in time_text:
        return time_text.split(" ", 1)[0][5:]  # MM-DD
    return time_text[5:] if len(time_text) >= 10 else time_text


def _pick_tick_indices(n: int, target: int = 8) -> list[int]:
    if n <= 0:
        return []
    if n <= target:
        return list(range(n))
    step = max(1, (n - 1) // (target - 1))
    idxs = list(range(0, n, step))
    if idxs[-1] != n - 1:
        idxs.append(n - 1)
    return idxs


def _volume_colors(bars: list[dict[str, Any]]) -> list[str]:
    """涨红跌绿（A 股习惯）。"""
    up, down = "#c43c3c", "#2e8b57"
    colors: list[str] = []
    for b in bars:
        o, c = float(b["open"]), float(b["close"])
        colors.append(up if c >= o else down)
    return colors


def _linreg_channel(
    closes: list[float],
    *,
    width: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    """收盘价线性回归中轴 + 平行上下轨（±width * 残差标准差）。"""
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
    """Donchian：滚动最高/最低，中轴取二者均值。"""
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


def _draw_channel(
    ax: Any,
    xs: list[int],
    bars: list[dict[str, Any]],
    closes: list[float],
    *,
    channel: str,
    window: int,
    width: float,
) -> str:
    """在价格轴上画通道；返回图例用的短标签。"""
    kind = (channel or "none").lower()
    if kind in {"", "none", "off"}:
        return ""

    n = len(xs)
    if n < 3:
        return ""

    # window<=0：用整段可见数据；否则只用最近 window 根拟合/计算。
    use_n = n if window <= 0 else min(window, n)
    start = n - use_n
    seg_x = xs[start:]

    if kind in {"reg", "regression", "linreg"}:
        mid, upper, lower = _linreg_channel(closes[start:], width=width)
        label = f"回归通道 ±{width:g}σ"
    elif kind in {"donchian", "dc"}:
        # Donchian 窗口：未指定时默认约 1 个交易日（5 分钟约 48 根）
        dc_win = use_n if window > 0 else min(48, use_n)
        mid, upper, lower = _donchian_channel(bars[start:], window=dc_win)
        label = f"Donchian({dc_win})"
    else:
        raise RuntimeError(f"未知通道类型: {channel!r}（可选 none/reg/donchian）")

    ax.plot(seg_x, mid, color="#1f4e79", linewidth=1.0, linestyle="--", label="中轴", zorder=3)
    ax.plot(seg_x, upper, color="#1f4e79", linewidth=1.0, label="上轨", zorder=3)
    ax.plot(seg_x, lower, color="#1f4e79", linewidth=1.0, label="下轨", zorder=3)
    ax.fill_between(seg_x, lower, upper, color="#1f4e79", alpha=0.08, zorder=2)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.85)
    return label


def plot_close_curve(
    kline: dict[str, Any],
    output: str | Path,
    *,
    channel: str = "reg",
    channel_window: int = 0,
    channel_width: float = 2.0,
) -> Path:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("画图需要安装 matplotlib：pip install 'emquote[plot]'") from exc

    _setup_chinese_font()

    bars = kline.get("bars") or []
    if not bars:
        raise RuntimeError("没有可绘制的 K 线数据")

    # 只用交易时段 bar，按序号等距排列，不按日历时间轴留空。
    xs = list(range(len(bars)))
    closes = [float(b["close"]) for b in bars]
    volumes = [float(b.get("volume") or 0) for b in bars]
    labels = [str(b["time"]) for b in bars]
    vol_colors = _volume_colors(bars)
    intraday = any(" " in t for t in labels)

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_price, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(11, 6.2),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.06},
        layout="constrained",
    )

    ax_price.plot(xs, closes, color="#c43c3c", linewidth=1.2, label="收盘价", zorder=4)
    ax_price.fill_between(xs, closes, min(closes), color="#c43c3c", alpha=0.06, zorder=1)

    channel_label = _draw_channel(
        ax_price,
        xs,
        bars,
        closes,
        channel=channel,
        window=channel_window,
        width=channel_width,
    )

    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  "
        f"{kline.get('interval') or ''} 价格/成交量（仅交易时段）"
    ).strip()
    if channel_label:
        title = f"{title}  ·  {channel_label}"
    ax_price.set_title(title)
    ax_price.set_ylabel("价格")
    ax_price.grid(True, alpha=0.25)
    ax_price.tick_params(labelbottom=False)

    width = 0.8 if len(xs) < 200 else 1.0
    ax_vol.bar(xs, volumes, width=width, color=vol_colors, align="center")
    ax_vol.set_ylabel("成交量")
    ax_vol.grid(True, axis="y", alpha=0.25)
    ax_vol.set_xlim(0, max(len(xs) - 1, 0))

    tick_idxs = _pick_tick_indices(len(xs), target=8)
    ax_vol.set_xticks(tick_idxs)
    ax_vol.set_xticklabels(
        [_label_for_bar(labels[i], intraday=intraday) for i in tick_idxs],
        rotation=30,
        ha="right",
    )

    fig.savefig(out)
    plt.close(fig)
    return out
