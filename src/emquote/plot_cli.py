"""两个面向 AI/脚本调用的画图 CLI。

- emplot-channel：价格通道图（回归 + Donchian）+ 条件单价
- emplot-pv：价量通道图（价量加权回归 + Donchian）+ 条件单价 + 触轨放量

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
from .plotting import plot_close_curve, print_condition_levels

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
    prog = "emplot-channel" if preset == "channel" else "emplot-pv"
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
    p.add_argument("--single-window", action="store_true", help="不切段，只画最近一段")
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
        path = plot_close_curve(
            k,
            out,
            channel=channel,
            channel_window=args.channel_window,
            channel_width=args.channel_width,
            show_levels=not args.no_levels,
            full_range=not args.single_window,
        )
        report = None
        if kinds != ["none"]:
            report = suggest_condition_orders(
                k,
                channels=kinds,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                full_range=not args.single_window,
            )

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
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"[{meta['title']}] 已保存: {path}")
            print(
                f"{k.get('name')} {k.get('symbol')}  {k.get('interval')}  "
                f"bars={len(k['bars'])}  channel={channel}"
            )
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


if __name__ == "__main__":
    raise SystemExit(main_channel())
