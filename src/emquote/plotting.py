"""把 K 线画成「上价格 / 下成交量」图（PNG）。

支持叠加多种通道，并可标注条件单参考价水平线。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .levels import compute_channel, parse_channels


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


def plot_close_curve(
    kline: dict[str, Any],
    output: str | Path,
    *,
    channel: str = "reg",
    channel_window: int = 0,
    channel_width: float = 2.0,
    show_levels: bool = True,
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
    channel_labels: list[str] = []

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_price, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(11, 6.4),
        dpi=140,
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.06},
        layout="constrained",
    )

    ax_price.plot(xs, closes, color="#c43c3c", linewidth=1.2, label="收盘价", zorder=5)
    ax_price.fill_between(xs, closes, min(closes), color="#c43c3c", alpha=0.05, zorder=1)

    for kind in kinds:
        if kind == "none":
            continue
        ch = compute_channel(bars, kind=kind, window=channel_window, width=channel_width)
        start = int(ch["start"])
        seg_x = xs[start:]
        color = ch["color"]
        mid = ch["mid"][start:]
        upper = ch["upper"][start:]
        lower = ch["lower"][start:]
        ax_price.plot(seg_x, mid, color=color, linewidth=1.0, linestyle="--", label=f"{ch['label']}中轴", zorder=3)
        ax_price.plot(seg_x, upper, color=color, linewidth=1.0, label=f"{ch['label']}上轨", zorder=3)
        ax_price.plot(seg_x, lower, color=color, linewidth=1.0, label=f"{ch['label']}下轨", zorder=3)
        ax_price.fill_between(seg_x, lower, upper, color=color, alpha=0.06, zorder=2)
        channel_labels.append(str(ch["label"]))

        if show_levels:
            ax_price.axhline(ch["last_lower"], color=color, linewidth=0.8, alpha=0.7, linestyle=":")
            ax_price.axhline(ch["last_upper"], color=color, linewidth=0.8, alpha=0.7, linestyle=":")
            ax_price.annotate(
                f"买参 {ch['last_lower']:.2f}",
                xy=(xs[-1], ch["last_lower"]),
                xytext=(-8, -10),
                textcoords="offset points",
                ha="right",
                fontsize=7,
                color=color,
            )
            ax_price.annotate(
                f"卖参 {ch['last_upper']:.2f}",
                xy=(xs[-1], ch["last_upper"]),
                xytext=(-8, 6),
                textcoords="offset points",
                ha="right",
                fontsize=7,
                color=color,
            )

    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  "
        f"{kline.get('interval') or ''} 价格/成交量（仅交易时段）"
    ).strip()
    if channel_labels:
        title = f"{title}  ·  {' + '.join(channel_labels)}"
    ax_price.set_title(title)
    ax_price.set_ylabel("价格")
    ax_price.grid(True, alpha=0.25)
    ax_price.tick_params(labelbottom=False)
    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.85, ncol=2)

    bar_w = 0.8 if len(xs) < 200 else 1.0
    ax_vol.bar(xs, volumes, width=bar_w, color=vol_colors, align="center")
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


def print_condition_levels(report: dict[str, Any]) -> None:
    print("东方财富条件单参考价（研究用，不下单）")
    print(
        f"标的: {report.get('name')} {report.get('symbol')}  "
        f"周期: {report.get('interval')}  最新: {report.get('last_close')}  "
        f"时间: {report.get('last_time')}"
    )
    print(f"说明: {report.get('disclaimer')}")
    print()
    for item in report.get("suggestions") or []:
        print(f"【{item['style']} · {item['label']}】")
        print(
            f"  下轨/中轴/上轨: {item['last_lower']} / {item['last_mid']} / {item['last_upper']}"
        )
        buy, sell, stop = item["buy"], item["sell"], item["stop"]
        print(f"  买入条件价: {buy['建议触发价']}  — {buy['用途']}；{buy['说明']}")
        print(f"  卖出条件价: {sell['建议触发价']}  — {sell['用途']}；{sell['说明']}")
        print(f"  止损参考价: {stop['建议触发价']}  — {stop['用途']}；{stop['说明']}")
        print()
