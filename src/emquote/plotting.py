"""把 K 线收盘价画成简单曲线图（PNG）。"""

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


def plot_close_curve(kline: dict[str, Any], output: str | Path) -> Path:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from datetime import datetime
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("画图需要安装 matplotlib：pip install 'emquote[plot]'") from exc

    _setup_chinese_font()

    bars = kline.get("bars") or []
    if not bars:
        raise RuntimeError("没有可绘制的 K 线数据")

    times = []
    for b in bars:
        t = b["time"]
        if " " in t:
            times.append(datetime.strptime(t, "%Y-%m-%d %H:%M"))
        else:
            times.append(datetime.strptime(t, "%Y-%m-%d"))
    closes = [b["close"] for b in bars]

    out = Path(output).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 4.8), dpi=140)
    ax.plot(times, closes, color="#c43c3c", linewidth=1.2)
    ax.fill_between(times, closes, min(closes), color="#c43c3c", alpha=0.08)
    title = f"{kline.get('name') or ''} {kline.get('symbol') or ''}  {kline.get('interval') or ''} 收盘价".strip()
    ax.set_title(title)
    ax.set_ylabel("价格")
    ax.grid(True, alpha=0.25)
    fmt = "%m-%d %H:%M" if " " in bars[0]["time"] else "%m-%d"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out
