"""把 K 线收盘价画成简单曲线图（PNG）。

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
    labels = [str(b["time"]) for b in bars]
    intraday = any(" " in t for t in labels)

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 4.8), dpi=140)
    ax.plot(xs, closes, color="#c43c3c", linewidth=1.2)
    ax.fill_between(xs, closes, min(closes), color="#c43c3c", alpha=0.08)

    title = (
        f"{kline.get('name') or ''} {kline.get('symbol') or ''}  "
        f"{kline.get('interval') or ''} 收盘价（仅交易时段）"
    ).strip()
    ax.set_title(title)
    ax.set_ylabel("价格")
    ax.grid(True, alpha=0.25)

    tick_idxs = _pick_tick_indices(len(xs), target=8)
    ax.set_xticks(tick_idxs)
    ax.set_xticklabels(
        [_label_for_bar(labels[i], intraday=intraday) for i in tick_idxs],
        rotation=30,
        ha="right",
    )
    ax.set_xlim(0, max(len(xs) - 1, 0))

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out
