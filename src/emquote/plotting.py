"""把 K 线画成「上价格 / 下成交量」图（PNG）。

分钟级数据按「交易 bar 序号」横轴绘制，跳过隔夜/周末等非交易时段，
避免休市空洞被连成斜线。
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


def plot_close_curve(kline: dict[str, Any], output: str | Path) -> Path:
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

    ax_price.plot(xs, closes, color="#c43c3c", linewidth=1.2)
    ax_price.fill_between(xs, closes, min(closes), color="#c43c3c", alpha=0.08)
    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  "
        f"{kline.get('interval') or ''} 价格/成交量（仅交易时段）"
    ).strip()
    ax_price.set_title(title)
    ax_price.set_ylabel("价格")
    ax_price.grid(True, alpha=0.25)
    ax_price.tick_params(labelbottom=False)

    # 成交量柱略窄，避免根数多时糊成一片。
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
