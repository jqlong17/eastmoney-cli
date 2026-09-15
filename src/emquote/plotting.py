"""把 K 线画成「上价格 / 下成交量」图（PNG）。

支持全时段自动分段通道，并标注条件单买入/卖出/止损参考价。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .captions import apply_caption, caption_for_channel
from .levels import compute_channel, parse_channels, suggest_condition_orders


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


def _volume_colors(bars: list[dict[str, Any]]) -> list[str]:
    up, down = "#c43c3c", "#2e8b57"
    return [up if float(b["close"]) >= float(b["open"]) else down for b in bars]


def _draw_segments(ax: Any, xs: list[int], segments: list[dict[str, Any]], *, kind: str) -> str:
    """画出某一通道类型的全部分段；图例只标一次。"""
    if not segments:
        return ""
    color = segments[0]["color"]
    label = segments[0]["label"].split("#")[0]
    labeled = False
    for seg in segments:
        start = int(seg["start"])
        end = int(seg["end"])
        seg_x = xs[start:end]
        mid = seg["mid"]
        upper = seg["upper"]
        lower = seg["lower"]
        legend = None if labeled else f"{label}"
        ax.plot(seg_x, mid, color=color, linewidth=1.0, linestyle="--", label=(f"{legend}中轴" if legend else None), zorder=3)
        ax.plot(seg_x, upper, color=color, linewidth=1.0, label=(f"{legend}上轨" if legend else None), zorder=3)
        ax.plot(seg_x, lower, color=color, linewidth=1.0, label=(f"{legend}下轨" if legend else None), zorder=3)
        ax.fill_between(seg_x, lower, upper, color=color, alpha=0.05, zorder=2)
        # 分段边界细竖线，便于看出自动切分
        if start > 0:
            ax.axvline(start, color=color, linewidth=0.6, alpha=0.25, linestyle=":")
        labeled = True
    return f"{label}×{len(segments)}段"


def _volume_ma(volumes: list[float], window: int = 20) -> list[float]:
    n = len(volumes)
    w = max(2, min(window, n))
    out: list[float] = []
    running = 0.0
    for i, v in enumerate(volumes):
        running += v
        if i >= w:
            running -= volumes[i - w]
            out.append(running / w)
        else:
            out.append(running / (i + 1))
    return out


def _draw_volume_panel(
    ax: Any,
    xs: list[int],
    volumes: list[float],
    vol_colors: list[str],
    *,
    closes: list[float],
    suggestion: dict[str, Any] | None,
) -> None:
    """成交量柱 + 均量线；高亮「触轨附近且放量」的 bar。"""
    bar_w = 0.8 if len(xs) < 200 else 1.0
    ax.bar(xs, volumes, width=bar_w, color=vol_colors, align="center", zorder=2)
    ma = _volume_ma(volumes, window=20)
    ax.plot(xs, ma, color="#264653", linewidth=1.0, label="量均线(20)", zorder=3)

    if suggestion and closes:
        buy_p = float(suggestion["buy_price"])
        sell_p = float(suggestion["sell_price"])
        band = max(abs(sell_p - buy_p) * 0.08, max(closes) * 0.0015, 0.02)
        highlight_x: list[int] = []
        highlight_y: list[float] = []
        for i, (c, v, m) in enumerate(zip(closes, volumes, ma)):
            near_rail = abs(c - buy_p) <= band or abs(c - sell_p) <= band
            if near_rail and m > 0 and v >= 1.5 * m:
                highlight_x.append(i)
                highlight_y.append(v)
        if highlight_x:
            ax.scatter(
                highlight_x,
                highlight_y,
                s=28,
                color="#e9c46a",
                edgecolors="#264653",
                linewidths=0.6,
                zorder=4,
                label="触轨放量",
            )

    ax.set_ylabel("成交量")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(loc="upper left", fontsize=7, framealpha=0.85)


def _draw_condition_levels(ax: Any, suggestion: dict[str, Any], x_right: int) -> None:
    """把条件单买/卖/止损价画成醒目水平线。"""
    buy_p = float(suggestion["buy_price"])
    sell_p = float(suggestion["sell_price"])
    stop_p = float(suggestion["stop_price"])
    mid_p = float(suggestion.get("last_mid") or 0)
    styles = [
        (buy_p, "#d62828", "条件单买入", 2.0),
        (sell_p, "#2a9d8f", "条件单卖出", 2.0),
        (stop_p, "#6c757d", "止损参考", 1.4),
    ]
    if mid_p > 0:
        styles.append((mid_p, "#457b9d", "中轴参考", 1.0))
    for price, color, name, lw in styles:
        ax.axhline(price, color=color, linewidth=lw, alpha=0.95, linestyle="-.", zorder=6)
        ax.annotate(
            f"{name} {price:.2f}",
            xy=(x_right, price),
            xytext=(-6, 0),
            textcoords="offset points",
            ha="right",
            va="center",
            fontsize=8,
            color=color,
            fontweight="bold",
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": color, "alpha": 0.85},
            zorder=7,
        )


def plot_close_curve(
    kline: dict[str, Any],
    output: str | Path,
    *,
    channel: str = "reg",
    channel_window: int = 0,
    channel_width: float = 2.0,
    show_levels: bool = True,
    full_range: bool = True,
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

    xs = list(range(len(bars)))
    closes = [float(b["close"]) for b in bars]
    volumes = [float(b.get("volume") or 0) for b in bars]
    labels = [str(b["time"]) for b in bars]
    vol_colors = _volume_colors(bars)
    intraday = any(" " in t for t in labels)
    kinds = parse_channels(channel)
    title_bits: list[str] = []

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_price, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(12, 6.8),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1], "hspace": 0.06},
        layout="constrained",
    )

    ax_price.plot(xs, closes, color="#c43c3c", linewidth=1.15, label="收盘价", zorder=5)
    ax_price.fill_between(xs, closes, min(closes), color="#c43c3c", alpha=0.04, zorder=1)

    primary_suggestion = None
    for kind in kinds:
        if kind == "none":
            continue
        packed = compute_channel(
            bars,
            kind=kind,
            window=channel_window,
            width=channel_width,
            interval=str(kline.get("interval") or "5m"),
            full_range=full_range,
        )
        segments = packed.get("segments") or [packed]
        bit = _draw_segments(ax_price, xs, segments, kind=kind)
        if bit:
            title_bits.append(bit)

    if show_levels and kinds != ["none"]:
        report = suggest_condition_orders(
            kline,
            channels=[k for k in kinds if k != "none"],
            channel_window=channel_window,
            channel_width=channel_width,
            full_range=full_range,
        )
        # 图上优先标注第一种通道的条件单价，避免多组水平线打架。
        if report.get("suggestions"):
            primary_suggestion = report["suggestions"][0]
            _draw_condition_levels(ax_price, primary_suggestion, xs[-1])

    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  "
        f"{kline.get('interval') or ''} 价格/成交量（仅交易时段）"
    ).strip()
    if title_bits:
        title = f"{title}  ·  {' + '.join(title_bits)}"
    if primary_suggestion:
        title = (
            f"{title}\n条件单参考  买 {primary_suggestion['buy_price']:.2f}  /  "
            f"卖 {primary_suggestion['sell_price']:.2f}  /  "
            f"止损 {primary_suggestion['stop_price']:.2f}"
            + (
                f"  /  中轴 {float(primary_suggestion['last_mid']):.2f}"
                if primary_suggestion.get("last_mid")
                else ""
            )
        )
    ax_price.set_title(title, fontsize=11)
    ax_price.set_ylabel("价格")
    ax_price.grid(True, alpha=0.25)
    ax_price.tick_params(labelbottom=False)
    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.85, ncol=2)

    _draw_volume_panel(
        ax_vol,
        xs,
        volumes,
        vol_colors,
        closes=closes,
        suggestion=primary_suggestion,
    )
    ax_vol.set_xlim(0, max(len(xs) - 1, 0))

    tick_idxs = _pick_tick_indices(len(xs), target=8)
    ax_vol.set_xticks(tick_idxs)
    ax_vol.set_xticklabels(
        [_label_for_bar(labels[i], intraday=intraday) for i in tick_idxs],
        rotation=30,
        ha="right",
    )

    apply_caption(
        fig,
        caption_for_channel(channel, has_levels=bool(primary_suggestion)),
        title="读图说明（价格/价量通道）",
    )
    fig.savefig(out)
    plt.close(fig)
    return out


def print_condition_levels(report: dict[str, Any]) -> None:
    print("东方财富条件单参考价（研究用，不下单）")
    print(
        f"标的: {report.get('name')} {report.get('symbol')}  "
        f"周期: {report.get('interval')}  最新: {report.get('last_close')}  "
        f"时间: {report.get('last_time')}"
    )
    print(f"说明: {report.get('disclaimer')}")
    wa = report.get("width_assessment") or {}
    if wa:
        print(
            f"宽度评估: {wa.get('label')} · {wa.get('reasonableness')}  "
            f"确定性分 {wa.get('certainty_score')}  "
            f"当前宽度 {wa.get('last_width_pct')}%"
        )
        if wa.get("plan_hint"):
            print(f"  → {wa.get('plan_hint')}")
    print()
    for item in report.get("suggestions") or []:
        print(f"【{item['style']} · {item['label']}】")
        print(
            f"  下轨/中轴/上轨: {item['last_lower']} / {item['last_mid']} / {item['last_upper']}"
        )
        if item.get("last_width_pct") is not None:
            cert = item.get("certainty") or {}
            print(
                f"  相对宽度: {item['last_width_pct']}%  "
                f"（{cert.get('label') or ''} · 分 {cert.get('certainty_score')}）"
            )
        buy, sell, stop = item["buy"], item["sell"], item["stop"]
        print(f"  买入条件价: {buy['建议触发价']}  — {buy['用途']}；{buy['说明']}")
        print(f"  卖出条件价: {sell['建议触发价']}  — {sell['用途']}；{sell['说明']}")
        print(f"  止损参考价: {stop['建议触发价']}  — {stop['用途']}；{stop['说明']}")
        print()


def plot_channel_width(
    kline: dict[str, Any],
    output: str | Path,
    *,
    channel: str = "vwreg,donchian",
    channel_window: int = 0,
    channel_width: float = 2.0,
    show_levels: bool = True,
    full_range: bool = True,
) -> Path:
    """通道宽度图：上价格+通道，下相对宽度%（不确定性）。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("画图需要安装 matplotlib：pip install 'emquote[plot]'") from exc

    from .levels import compute_channel_width, assess_width_for_plan

    _setup_chinese_font()

    bars = kline.get("bars") or []
    if not bars:
        raise RuntimeError("没有可绘制的 K 线数据")

    xs = list(range(len(bars)))
    closes = [float(b["close"]) for b in bars]
    labels = [str(b["time"]) for b in bars]
    intraday = any(" " in t for t in labels)
    kinds = [k for k in parse_channels(channel) if k != "none"]
    if not kinds:
        kinds = ["vwreg"]

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_price, ax_w) = plt.subplots(
        2,
        1,
        figsize=(12, 7.2),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [2.6, 1.4], "hspace": 0.08},
        layout="constrained",
    )

    ax_price.plot(xs, closes, color="#c43c3c", linewidth=1.15, label="收盘价", zorder=5)

    width_reports: list[dict[str, Any]] = []
    title_bits: list[str] = []
    for kind in kinds:
        packed = compute_channel(
            bars,
            kind=kind,
            window=channel_window,
            width=channel_width,
            interval=str(kline.get("interval") or "5m"),
            full_range=full_range,
        )
        segments = packed.get("segments") or [packed]
        bit = _draw_segments(ax_price, xs, segments, kind=kind)
        if bit:
            title_bits.append(bit)

        wrep = compute_channel_width(
            bars,
            kind=kind,
            window=channel_window,
            width=channel_width,
            interval=str(kline.get("interval") or "5m"),
            full_range=full_range,
        )
        width_reports.append(wrep)
        series = wrep["series_pct"]
        ys = [float(v) if v is not None else float("nan") for v in series]
        color = wrep.get("color") or "#333333"
        base_label = str(wrep["label"]).split("×")[0]
        ax_w.plot(
            xs,
            ys,
            color=color,
            linewidth=1.25,
            label=f"{base_label} 宽度%",
            zorder=4,
        )
        ax_w.fill_between(xs, 0, ys, color=color, alpha=0.12, zorder=2)

    assessment = assess_width_for_plan(width_reports)
    primary = width_reports[0]
    cert = primary.get("certainty") or {}
    p33 = cert.get("p33_pct")
    p66 = cert.get("p66_pct")
    p50 = cert.get("p50_pct")
    if p33 is not None and p66 is not None:
        ax_w.axhspan(0, float(p33), color="#2a9d8f", alpha=0.08, zorder=1, label="窄区(≤P33)")
        ax_w.axhspan(float(p66), max(float(p66) * 1.4, float(primary.get("max_width_pct") or p66) * 1.05),
                     color="#e76f51", alpha=0.08, zorder=1, label="宽区(≥P66)")
    if p50 is not None:
        ax_w.axhline(float(p50), color="#6c757d", linewidth=1.0, linestyle="--", alpha=0.85, label=f"中位 {p50:.2f}%")

    primary_suggestion = None
    if show_levels:
        report = suggest_condition_orders(
            kline,
            channels=kinds,
            channel_window=channel_window,
            channel_width=channel_width,
            full_range=full_range,
        )
        if report.get("suggestions"):
            primary_suggestion = report["suggestions"][0]
            _draw_condition_levels(ax_price, primary_suggestion, xs[-1])

    last_w = assessment.get("last_width_pct")
    score = assessment.get("certainty_score")
    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  "
        f"{kline.get('interval') or ''} 通道宽度（不确定性）"
    ).strip()
    if title_bits:
        title = f"{title}  ·  {' + '.join(title_bits)}"
    title = (
        f"{title}\n"
        f"当前宽度 {last_w}%  ·  {assessment.get('label')}  ·  "
        f"确定性分 {score}  ·  条件单{assessment.get('reasonableness')}"
    )
    if primary_suggestion:
        title = (
            f"{title}\n条件单参考  买 {primary_suggestion['buy_price']:.2f}  /  "
            f"卖 {primary_suggestion['sell_price']:.2f}  /  "
            f"止损 {primary_suggestion['stop_price']:.2f}"
        )
    ax_price.set_title(title, fontsize=11)
    ax_price.set_ylabel("价格")
    ax_price.grid(True, alpha=0.25)
    ax_price.tick_params(labelbottom=False)
    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.85, ncol=2)

    ax_w.set_ylabel("相对宽度 %")
    ax_w.set_xlabel("")
    ax_w.grid(True, alpha=0.25)
    ax_w.legend(loc="upper left", fontsize=7, framealpha=0.85, ncol=2)
    ax_w.set_xlim(0, max(len(xs) - 1, 0))
    # 给一点顶部余量
    ymax = max((v for v in [primary.get("max_width_pct"), last_w, p66] if v is not None), default=1.0)
    ax_w.set_ylim(0, max(float(ymax) * 1.15, 0.5))

    tick_idxs = _pick_tick_indices(len(xs), target=8)
    ax_w.set_xticks(tick_idxs)
    ax_w.set_xticklabels(
        [_label_for_bar(labels[i], intraday=intraday) for i in tick_idxs],
        rotation=30,
        ha="right",
    )

    from .captions import caption_for_width

    apply_caption(
        fig,
        caption_for_width(assessment),
        title="读图说明（通道宽度 / 确定性）",
    )
    fig.savefig(out)
    plt.close(fig)
    return out
