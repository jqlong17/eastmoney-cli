"""分析用核心图：K 线蜡烛、日线均线、分时。"""

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
    if intraday and " " in time_text:
        date, hm = time_text.split(" ", 1)
        return f"{date[5:]} {hm}"
    if " " in time_text:
        return time_text.split(" ", 1)[0][5:]
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


def _sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        if i + 1 < window:
            out.append(None)
        else:
            out.append(running / window)
    return out


def _volume_colors(bars: list[dict[str, Any]]) -> list[str]:
    up, down = "#c43c3c", "#2e8b57"
    return [up if float(b["close"]) >= float(b["open"]) else down for b in bars]


def _draw_candles(ax: Any, bars: list[dict[str, Any]], *, width: float = 0.6) -> None:
    from matplotlib.patches import Rectangle

    up, down = "#c43c3c", "#2e8b57"
    for i, b in enumerate(bars):
        o = float(b["open"])
        h = float(b["high"])
        low = float(b["low"])
        c = float(b["close"])
        color = up if c >= o else down
        ax.vlines(i, low, h, color=color, linewidth=0.9, zorder=3)
        body_low = min(o, c)
        body_h = abs(c - o)
        if body_h < 1e-8:
            body_h = max(abs(h - low) * 0.02, abs(c) * 0.0003, 0.01)
            body_low = c - body_h / 2
        ax.add_patch(
            Rectangle(
                (i - width / 2, body_low),
                width,
                body_h,
                facecolor=color,
                edgecolor=color,
                linewidth=0.6,
                zorder=4,
            )
        )


def _draw_ma_lines(ax: Any, closes: list[float], windows: list[int]) -> list[str]:
    colors = {5: "#1d3557", 10: "#e76f51", 20: "#2a9d8f", 60: "#9b5de5", 120: "#f4a261"}
    bits: list[str] = []
    xs = list(range(len(closes)))
    for w in windows:
        series = _sma(closes, w)
        ys = [v if v is not None else float("nan") for v in series]
        color = colors.get(w, "#333333")
        ax.plot(xs, ys, linewidth=1.05, color=color, label=f"MA{w}", zorder=5)
        last = next((v for v in reversed(series) if v is not None), None)
        if last is not None:
            bits.append(f"MA{w}={last:.2f}")
    return bits


def _finish_axes(
    fig: Any,
    ax_price: Any,
    ax_vol: Any,
    bars: list[dict[str, Any]],
    title: str,
    out: Path,
) -> Path:
    import matplotlib.pyplot as plt

    labels = [str(b["time"]) for b in bars]
    volumes = [float(b.get("volume") or 0) for b in bars]
    xs = list(range(len(bars)))
    intraday = any(" " in t for t in labels)
    ax_vol.bar(
        xs,
        volumes,
        width=0.8 if len(xs) < 200 else 1.0,
        color=_volume_colors(bars),
        align="center",
    )
    ax_vol.set_ylabel("成交量")
    ax_vol.grid(True, axis="y", alpha=0.25)
    ax_vol.set_xlim(-0.5, max(len(xs) - 0.5, 0.5))
    tick_idxs = _pick_tick_indices(len(xs), target=8)
    ax_vol.set_xticks(tick_idxs)
    ax_vol.set_xticklabels(
        [_label_for_bar(labels[i], intraday=intraday) for i in tick_idxs],
        rotation=30,
        ha="right",
    )
    ax_price.set_title(title, fontsize=11)
    ax_price.set_ylabel("价格")
    ax_price.grid(True, alpha=0.25)
    ax_price.tick_params(labelbottom=False)
    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.85, ncol=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out


def plot_candles(
    kline: dict[str, Any],
    output: str | Path,
    *,
    ma: list[int] | None = None,
    title_suffix: str = "K线（蜡烛）",
) -> Path:
    """蜡烛图 + 成交量 + 可选均线。"""
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
    ma_windows = [5, 10, 20] if ma is None else ma
    closes = [float(b["close"]) for b in bars]
    out = Path(output).expanduser().resolve()

    fig, (ax_price, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(12, 6.8),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1], "hspace": 0.06},
        layout="constrained",
    )
    _draw_candles(ax_price, bars)
    ma_bits = _draw_ma_lines(ax_price, closes, ma_windows) if ma_windows else []
    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  "
        f"{kline.get('interval') or ''} {title_suffix}"
    ).strip()
    if ma_bits:
        title = f"{title}\n{'  '.join(ma_bits)}"
    return _finish_axes(fig, ax_price, ax_vol, bars, title, out)


def plot_daily(
    kline: dict[str, Any],
    output: str | Path,
    *,
    ma: list[int] | None = None,
) -> Path:
    """日线蜡烛 + MA5/10/20/60 + 成交量。"""
    windows = [5, 10, 20, 60] if ma is None else ma
    return plot_candles(kline, output, ma=windows, title_suffix="日线趋势")


def _last_session_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not bars:
        return []
    last_day = str(bars[-1]["time"])[:10]
    return [b for b in bars if str(b["time"])[:10] == last_day]


def _vwap(bars: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    cum_pv = 0.0
    cum_v = 0.0
    for b in bars:
        typical = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        vol = max(float(b.get("volume") or 0), 1e-9)
        cum_pv += typical * vol
        cum_v += vol
        out.append(cum_pv / cum_v)
    return out


def plot_intraday(
    kline: dict[str, Any],
    output: str | Path,
    *,
    pre_close: float | None = None,
    last_day_only: bool = True,
) -> Path:
    """分时图：最新交易日价格线 + VWAP + 昨收 + 成交量。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("画图需要安装 matplotlib：pip install 'emquote[plot]'") from exc

    _setup_chinese_font()
    bars_all = kline.get("bars") or []
    bars = _last_session_bars(bars_all) if last_day_only else list(bars_all)
    if not bars:
        raise RuntimeError("没有可绘制的分时数据")

    xs = list(range(len(bars)))
    closes = [float(b["close"]) for b in bars]
    vwap = _vwap(bars)
    if pre_close is None:
        if last_day_only and len(bars_all) > len(bars):
            pre_close = float(bars_all[-(len(bars) + 1)]["close"])
        else:
            pre_close = float(bars[0]["open"])

    out = Path(output).expanduser().resolve()
    fig, (ax_price, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(12, 6.4),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [3.0, 1], "hspace": 0.06},
        layout="constrained",
    )
    ax_price.plot(xs, closes, color="#c43c3c", linewidth=1.2, label="分时价", zorder=5)
    ax_price.plot(xs, vwap, color="#1d3557", linewidth=1.05, linestyle="--", label="VWAP", zorder=4)
    ax_price.axhline(
        float(pre_close),
        color="#6c757d",
        linewidth=1.1,
        linestyle="-.",
        label=f"昨收 {float(pre_close):.2f}",
        zorder=3,
    )
    ax_price.fill_between(
        xs,
        closes,
        float(pre_close),
        where=[c >= float(pre_close) for c in closes],
        color="#c43c3c",
        alpha=0.08,
        interpolate=True,
    )
    ax_price.fill_between(
        xs,
        closes,
        float(pre_close),
        where=[c < float(pre_close) for c in closes],
        color="#2e8b57",
        alpha=0.08,
        interpolate=True,
    )
    last = closes[-1]
    ax_price.annotate(
        f"{last:.2f}",
        xy=(xs[-1], last),
        xytext=(6, 0),
        textcoords="offset points",
        va="center",
        fontsize=9,
        color="#c43c3c",
        fontweight="bold",
    )
    day = str(bars[-1]["time"])[:10]
    chg = (last / float(pre_close) - 1.0) * 100 if pre_close else 0.0
    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  分时 {day}  "
        f"现价 {last:.2f}  昨收 {float(pre_close):.2f}  涨跌 {chg:+.2f}%"
    ).strip()
    return _finish_axes(fig, ax_price, ax_vol, bars, title, out)
