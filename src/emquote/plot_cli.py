"""面向 AI/脚本调用的画图 CLI。

通道图：
- emplot-channel：价格通道（回归 + Donchian）+ 条件单价
- emplot-pv：价量通道（价量加权回归 + Donchian）+ 条件单价 + 触轨放量
- emplot-width：通道宽度 / 确定性（相对宽度%）+ 条件单合理性评估
- emplot-ke：价量动能（½mv²）+ 推进/耗散评估

分析图：
- emplot-kline：蜡烛 K 线 + MA
- emplot-daily：日线趋势 + MA5/10/20/60
- emplot-intraday：分时 + VWAP + 昨收

刻意参数少、默认值固定，方便 agent 直接调用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .client import EastMoneyClient, QuoteError
from .levels import parse_channels, suggest_condition_orders
from .plotting import plot_channel_width, plot_close_curve, plot_kinetic_energy, print_condition_levels

# 与 README 示例图一致的预设
PRESETS: dict[str, dict[str, Any]] = {
    "channel": {
        "title": "价格通道图",
        "channel": "reg,donchian",
        "channel_window": 96,
        "channel_width": 2.0,
        "default_output": "emquote-channel.png",
        "help": "价格通道（线性回归 + Donchian）+ 条件单买/卖/止损标注",
    },
    "pv": {
        "title": "价量通道图",
        "channel": "vwreg,donchian",
        "channel_window": 96,
        "channel_width": 2.0,
        "default_output": "emquote-price-volume.png",
        "help": "价量加权回归 + Donchian + 量均线/触轨放量 + 条件单标注",
    },
    "width": {
        "title": "通道宽度图",
        "channel": "vwreg,donchian",
        "channel_window": 96,
        "channel_width": 2.0,
        "default_output": "emquote-width.png",
        "help": "相对宽度%体现波动/分歧；窄=确定性偏高，宽=不确定性偏高；辅助评估条件单",
    },
    "ke": {
        "title": "价量动能图",
        "channel": "vwreg",
        "channel_window": 96,
        "channel_width": 2.0,
        "default_output": "emquote-ke.png",
        "help": "物理隐喻：质量≈相对成交量，速度≈涨跌，KE≈½mv²；看推进/耗散以评估条件单",
    },
}


def _default_output(preset: str, symbol: str | None) -> str:
    base = PRESETS[preset]["default_output"]
    if not symbol:
        return base
    code = symbol.split(".")[0].replace("/", "-")
    stem = Path(base).stem
    return f"{code}-{stem.replace('emquote-', '')}.png"


def _build_parser(preset: str) -> argparse.ArgumentParser:
    meta = PRESETS[preset]
    prog = {
        "channel": "emplot-channel",
        "pv": "emplot-pv",
        "width": "emplot-width",
        "ke": "emplot-ke",
    }[preset]
    p = argparse.ArgumentParser(
        prog=prog,
        description=f"{meta['title']}：{meta['help']}。只读研究，不下单。",
    )
    p.add_argument(
        "symbol",
        nargs="?",
        default=None,
        help="股票代码，如 603606.SH；使用 --from-json 时可省略",
    )
    p.add_argument("-o", "--output", default=None, help="输出 PNG 路径（默认按代码自动命名）")
    p.add_argument("-i", "--interval", default="5m", help="K 线周期，默认 5m")
    p.add_argument("--days", type=int, default=10, help="最近 N 个交易日，默认 10")
    p.add_argument("--bars", type=int, default=None, help="只保留最近 N 根")
    p.add_argument("--adjust", choices=["none", "qfq", "hfq"], default="none", help="复权")
    p.add_argument("--from-json", dest="from_json", help="离线读取东财原始 JSON")
    p.add_argument("--refresh", action="store_true", help="忽略本地 K 线缓存，强制重拉")
    p.add_argument("--no-cache", action="store_true", help="禁用 K 线本地缓存")
    p.add_argument(
        "--channel-window",
        type=int,
        default=meta["channel_window"],
        help=f"分段长度（根数），默认 {meta['channel_window']}",
    )
    p.add_argument(
        "--channel-width",
        type=float,
        default=meta["channel_width"],
        help=f"回归带宽（σ 倍数），默认 {meta['channel_width']}",
    )
    p.add_argument("--single-window", action="store_true", help="只画最近一个窗口（不覆盖全时段）")
    p.add_argument(
        "--retrospective-channel",
        action="store_true",
        help="使用旧的段内全样本回顾拟合（后视镜）；默认改为因果滚动通道",
    )
    p.add_argument("--no-levels", action="store_true", help="不在图上标注条件单价")
    p.add_argument(
        "--json",
        action="store_true",
        help="额外把条件单参考价以 JSON 打到 stdout（便于 AI 解析）",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="请求超时秒数")
    p.add_argument("--retries", type=int, default=3, help="每主机重试次数")
    return p


def _load_kline(client: EastMoneyClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.from_json:
        payload = json.loads(Path(args.from_json).expanduser().read_text(encoding="utf-8"))
        return EastMoneyClient.kline_from_payload(payload, days=args.days, bars=args.bars)
    if not args.symbol:
        raise QuoteError("请提供股票代码，或使用 --from-json")
    return client.kline(
        args.symbol,
        interval=args.interval,
        days=args.days,
        bars=args.bars,
        adjust=args.adjust,
        use_cache=not getattr(args, "no_cache", False),
        refresh=bool(getattr(args, "refresh", False)),
    )


def _run_plot(preset: str, argv: list[str] | None = None) -> int:
    meta = PRESETS[preset]
    args = _build_parser(preset).parse_args(argv)
    client = EastMoneyClient(timeout=args.timeout, retries=args.retries)
    channel = meta["channel"]
    kinds = parse_channels(channel)

    try:
        k = _load_kline(client, args)
        out = args.output or _default_output(preset, args.symbol or k.get("symbol"))
        causal = not bool(getattr(args, "retrospective_channel", False))
        if preset == "width":
            path = plot_channel_width(
                k,
                out,
                channel=channel,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                show_levels=not args.no_levels,
                full_range=not args.single_window,
                causal=causal,
            )
        elif preset == "ke":
            path = plot_kinetic_energy(
                k,
                out,
                channel=channel,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                show_levels=not args.no_levels,
                full_range=not args.single_window,
                causal=causal,
            )
        else:
            path = plot_close_curve(
                k,
                out,
                channel=channel,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                show_levels=not args.no_levels,
                full_range=not args.single_window,
                causal=causal,
            )
        report = None
        energy = None
        if kinds != ["none"]:
            report = suggest_condition_orders(
                k,
                channels=kinds,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                full_range=not args.single_window,
                causal=causal,
            )
        if preset == "ke":
            from .energy import compute_kinetic_energy

            energy = compute_kinetic_energy(k.get("bars") or [])

        if args.json:
            payload = {
                "preset": preset,
                "title": meta["title"],
                "channel": channel,
                "output": str(path),
                "symbol": k.get("symbol"),
                "name": k.get("name"),
                "interval": k.get("interval"),
                "bars": len(k.get("bars") or []),
                "levels": report,
                "width_assessment": (report or {}).get("width_assessment"),
                "energy": {
                    "last_ke": energy.get("last_ke"),
                    "last_mass": energy.get("last_mass"),
                    "last_velocity": energy.get("last_velocity"),
                    "assessment": energy.get("assessment"),
                    "p33": energy.get("p33"),
                    "p50": energy.get("p50"),
                    "p66": energy.get("p66"),
                }
                if energy
                else None,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"[{meta['title']}] 已保存: {path}")
            print(
                f"{k.get('name')} {k.get('symbol')}  {k.get('interval')}  "
                f"bars={len(k['bars'])}  channel={channel}"
            )
            if energy:
                a = energy.get("assessment") or {}
                print(
                    f"动能: KE={energy.get('last_ke')}  {a.get('label')}  "
                    f"可操作性={a.get('operability_score')}  ·  {a.get('reasonableness')}"
                )
                if a.get("plan_hint"):
                    print(f"  → {a.get('plan_hint')}")
            if report:
                print()
                print_condition_levels(report)
        return 0
    except (QuoteError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1


def main_channel(argv: list[str] | None = None) -> int:
    """emplot-channel 入口。"""
    return _run_plot("channel", argv)


def main_pv(argv: list[str] | None = None) -> int:
    """emplot-pv 入口。"""
    return _run_plot("pv", argv)


def main_width(argv: list[str] | None = None) -> int:
    """emplot-width 入口。"""
    return _run_plot("width", argv)


def main_ke(argv: list[str] | None = None) -> int:
    """emplot-ke 入口。"""
    return _run_plot("ke", argv)


if __name__ == "__main__":
    raise SystemExit(main_channel())


# --- 分析图预设（K线 / 日线 / 分时）---
CHART_PRESETS: dict[str, dict[str, Any]] = {
    "kline": {
        "title": "K线蜡烛图",
        "default_interval": "5m",
        "default_days": 10,
        "default_output": "emquote-kline.png",
        "ma": [5, 10, 20],
        "help": "OHLC 蜡烛 + 成交量 + MA5/10/20，看短线形态",
    },
    "daily": {
        "title": "日线趋势图",
        "default_interval": "1d",
        "default_days": 120,
        "default_output": "emquote-daily.png",
        "ma": [5, 10, 20, 60],
        "help": "日线蜡烛 + MA5/10/20/60 + 成交量，看大方向",
    },
    "intraday": {
        "title": "分时图",
        "default_interval": "1m",
        "default_days": 2,
        "default_output": "emquote-intraday.png",
        "ma": [],
        "help": "最新交易日分时价 + VWAP + 昨收 + 成交量",
    },
}


def _chart_default_output(preset: str, symbol: str | None) -> str:
    base = CHART_PRESETS[preset]["default_output"]
    if not symbol:
        return base
    code = symbol.split(".")[0].replace("/", "-")
    stem = Path(base).stem
    return f"{code}-{stem.replace('emquote-', '')}.png"


def _build_chart_parser(preset: str) -> argparse.ArgumentParser:
    meta = CHART_PRESETS[preset]
    prog = {"kline": "emplot-kline", "daily": "emplot-daily", "intraday": "emplot-intraday"}[preset]
    p = argparse.ArgumentParser(
        prog=prog,
        description=f"{meta['title']}：{meta['help']}。只读研究，不下单。",
    )
    p.add_argument(
        "symbol",
        nargs="?",
        default=None,
        help="股票代码，如 603606.SH；使用 --from-json 时可省略",
    )
    p.add_argument("-o", "--output", default=None, help="输出 PNG 路径（默认按代码自动命名）")
    p.add_argument(
        "-i",
        "--interval",
        default=meta["default_interval"],
        help=f"K 线周期，默认 {meta['default_interval']}",
    )
    p.add_argument(
        "--days",
        type=int,
        default=meta["default_days"],
        help=f"最近 N 个交易日，默认 {meta['default_days']}",
    )
    p.add_argument("--bars", type=int, default=None, help="只保留最近 N 根")
    p.add_argument("--adjust", choices=["none", "qfq", "hfq"], default="none", help="复权")
    p.add_argument("--from-json", dest="from_json", help="离线读取东财原始 JSON")
    p.add_argument("--refresh", action="store_true", help="忽略本地 K 线缓存，强制重拉")
    p.add_argument("--no-cache", action="store_true", help="禁用 K 线本地缓存")
    p.add_argument(
        "--ma",
        default=None,
        help="均线窗口，逗号分隔；kline 默认 5,10,20；daily 默认 5,10,20,60；intraday 忽略",
    )
    p.add_argument(
        "--pre-close",
        type=float,
        default=None,
        help="分时昨收（不传则自动用上一根收盘近似）",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="额外把摘要 JSON 打到 stdout（便于 AI 解析）",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="请求超时秒数")
    p.add_argument("--retries", type=int, default=3, help="每主机重试次数")
    return p


def _parse_ma(raw: str | None, default: list[int]) -> list[int]:
    if raw is None or raw.strip() == "":
        return list(default)
    if raw.strip().lower() in {"none", "off", "0"}:
        return []
    out: list[int] = []
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def _run_chart(preset: str, argv: list[str] | None = None) -> int:
    from .charts import plot_candles, plot_daily, plot_intraday

    meta = CHART_PRESETS[preset]
    args = _build_chart_parser(preset).parse_args(argv)
    client = EastMoneyClient(timeout=args.timeout, retries=args.retries)

    try:
        k = _load_kline(client, args)
        out = args.output or _chart_default_output(preset, args.symbol or k.get("symbol"))
        ma = _parse_ma(args.ma, list(meta["ma"]))

        if preset == "kline":
            path = plot_candles(k, out, ma=ma)
        elif preset == "daily":
            path = plot_daily(k, out, ma=ma)
        else:
            path = plot_intraday(k, out, pre_close=args.pre_close)

        bars = k.get("bars") or []
        last = bars[-1] if bars else {}
        summary = {
            "preset": preset,
            "title": meta["title"],
            "output": str(path),
            "symbol": k.get("symbol"),
            "name": k.get("name"),
            "interval": k.get("interval"),
            "bars": len(bars),
            "last_time": last.get("time"),
            "last_close": last.get("close"),
            "ma": ma if preset != "intraday" else [],
        }
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"[{meta['title']}] 已保存: {path}")
            print(
                f"{k.get('name')} {k.get('symbol')}  {k.get('interval')}  "
                f"bars={len(bars)}  last={last.get('close')}"
            )
        return 0
    except (QuoteError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1


def main_kline(argv: list[str] | None = None) -> int:
    """emplot-kline 入口。"""
    return _run_chart("kline", argv)


def main_daily(argv: list[str] | None = None) -> int:
    """emplot-daily 入口。"""
    return _run_chart("daily", argv)


def main_intraday(argv: list[str] | None = None) -> int:
    """emplot-intraday 入口。"""
    return _run_chart("intraday", argv)
