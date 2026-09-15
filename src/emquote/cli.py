"""emquote 命令行入口。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from .calibrate import calibrate_multi
from .client import EastMoneyClient, QuoteError
from .levels import parse_channels, suggest_condition_orders
from .plan import build_condition_plan, print_condition_plan
from .plotting import plot_close_curve, print_condition_levels
from .plot_cli import (
    main_channel,
    main_daily,
    main_intraday,
    main_ke,
    main_kline,
    main_pv,
    main_width,
)


def _print_quote(q: dict[str, Any]) -> None:
    print("东方财富公开行情（只读）")
    print(f"代码: {q['code']}  名称: {q['name']}  ({q['symbol']})")
    print(f"最新: {q['last']}  时间: {q['time']}")
    print(f"涨跌: {q['change']}  涨跌幅: {q['pct']}%")
    print(f"今开: {q['open']}  最高: {q['high']}  最低: {q['low']}  昨收: {q['pre_close']}")
    print(f"来源: {q['source']}")


def _print_kline(k: dict[str, Any], limit: int = 12) -> None:
    bars = k["bars"]
    print("东方财富公开 K 线（只读）")
    print(f"代码: {k['code']}  名称: {k['name']}  周期: {k['interval']}  复权: {k['adjust']}")
    print(f"根数: {len(bars)}  来源: {k['source']}")
    if not bars:
        print("（无数据）")
        return
    print("时间                 开盘      收盘      最高      最低")
    show = bars if len(bars) <= limit else bars[:3] + bars[-min(limit - 3, len(bars) - 3) :]
    skipped = len(bars) > limit
    for i, row in enumerate(show):
        if skipped and i == 3:
            print("  ...")
        print(
            f"{row['time']:<19} {row['open']:>8.2f} {row['close']:>8.2f} "
            f"{row['high']:>8.2f} {row['low']:>8.2f}"
        )


def _emit_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _emit_csv(bars: list[dict[str, Any]]) -> None:
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=["time", "open", "close", "high", "low", "volume", "amount"],
    )
    writer.writeheader()
    for row in bars:
        writer.writerow(row)


def _load_kline_json(path: str, days: int | None, bars: int | None) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return EastMoneyClient.kline_from_payload(payload, days=days, bars=bars)


def _add_kline_fetch_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("symbol", nargs="?", default=None, help="股票代码；使用 --from-json 时可省略")
    sp.add_argument("-i", "--interval", default="5m", help="周期，默认 5m")
    sp.add_argument("--days", type=int, default=10, help="最近 N 个交易日，默认 10")
    sp.add_argument("--bars", type=int, default=None, help="只保留最近 N 根")
    sp.add_argument("--adjust", choices=["none", "qfq", "hfq"], default="qfq", help="复权，默认 qfq（波段研究）")
    sp.add_argument("--from-json", dest="from_json", help="离线读取东财原始 JSON")
    sp.add_argument(
        "--refresh",
        action="store_true",
        help="忽略本地 K 线缓存，强制向东财重新拉取",
    )
    sp.add_argument(
        "--no-cache",
        action="store_true",
        help="禁用 K 线本地缓存（成功也不写入，失败不回退）",
    )


def _add_channel_args(sp: argparse.ArgumentParser, *, default: str) -> None:
    sp.add_argument(
        "--channel",
        default=default,
        help="通道：none/reg/vwreg/donchian/hl，可组合如 vwreg,donchian 或 reg+hl",
    )
    sp.add_argument(
        "--channel-window",
        type=int,
        default=0,
        help="分段长度（根数）。画全时段时自动切成多段；0=按周期自动估计（5m 约 1 日）",
    )
    sp.add_argument(
        "--channel-width",
        type=float,
        default=2.0,
        help="回归通道宽度（残差标准差倍数），默认 2",
    )
    sp.add_argument(
        "--single-window",
        action="store_true",
        help="不切段，只画最近一个窗口的通道（旧行为）",
    )


def _fetch_kline(client: EastMoneyClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.from_json:
        return _load_kline_json(args.from_json, args.days, args.bars)
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="emquote",
        description="东方财富公开行情只读 CLI：报价 / K 线 / 画图 / 波段条件单方案。服务几天到几周、用条件单代替盯盘；非交易接口。",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="单次请求超时秒数")
    p.add_argument("--retries", type=int, default=3, help="每主机重试次数")
    sub = p.add_subparsers(dest="cmd", required=True)

    pq = sub.add_parser("quote", help="拉取实时报价")
    pq.add_argument("symbol", help="如 603606.SH / 002223.SZ / 603606")
    pq.add_argument("--json", action="store_true", help="输出 JSON")

    pk = sub.add_parser("kline", help="拉取 K 线 / 成交价序列")
    _add_kline_fetch_args(pk)
    pk.add_argument("--json", action="store_true", help="输出 JSON")
    pk.add_argument("--csv", action="store_true", help="输出 CSV 到 stdout")

    pp = sub.add_parser("plot", help="绘制价格/成交量/通道图并保存 PNG（通用，可自选通道）")
    _add_kline_fetch_args(pp)
    pp.add_argument("-o", "--output", default="emquote-chart.png", help="输出 PNG 路径")
    _add_channel_args(pp, default="reg")
    pp.add_argument("--no-levels", action="store_true", help="不在图上标注条件单参考价")

    sub.add_parser(
        "plot-channel",
        help="【AI 推荐】价格通道图 CLI 别名 → 同 emplot-channel",
        add_help=False,
    )
    sub.add_parser(
        "plot-pv",
        help="【AI 推荐】价量通道图 CLI 别名 → 同 emplot-pv",
        add_help=False,
    )
    sub.add_parser(
        "plot-width",
        help="【AI 推荐】通道宽度/确定性图 CLI 别名 → 同 emplot-width",
        add_help=False,
    )
    sub.add_parser(
        "plot-ke",
        help="【AI 推荐】价量动能图 CLI 别名 → 同 emplot-ke",
        add_help=False,
    )
    sub.add_parser(
        "plot-kline",
        help="【AI 推荐】K线蜡烛图 CLI 别名 → 同 emplot-kline",
        add_help=False,
    )
    sub.add_parser(
        "plot-daily",
        help="【AI 推荐】日线趋势图 CLI 别名 → 同 emplot-daily",
        add_help=False,
    )
    sub.add_parser(
        "plot-intraday",
        help="【AI 推荐】分时图 CLI 别名 → 同 emplot-intraday",
        add_help=False,
    )

    pl = sub.add_parser(
        "levels",
        help="根据通道给出东方财富条件单买入/卖出参考价（只打印，不下单）",
    )
    _add_kline_fetch_args(pl)
    _add_channel_args(pl, default="vwreg,donchian")
    pl.add_argument("--json", action="store_true", help="输出 JSON")

    ppn = sub.add_parser(
        "plan",
        help="输出标准化波段条件单方案：买入区/止盈/止损/有效期/失效条件/历史校准（不下单）",
    )
    _add_kline_fetch_args(ppn)
    _add_channel_args(ppn, default="vwreg,donchian")
    ppn.add_argument("--json", action="store_true", help="输出 JSON")
    ppn.add_argument(
        "--no-calibrate",
        action="store_true",
        help="跳过 walk-forward 历史校准（更快，但不输出命中后验）",
    )
    ppn.add_argument(
        "--scan",
        action="store_true",
        help="附加 window×σ 参数稳定性扫描（更慢）",
    )
    ppn.add_argument(
        "--capital",
        type=float,
        default=None,
        help="账户资金（元），用于按风险预算估算股数",
    )
    ppn.add_argument(
        "--risk-pct",
        type=float,
        default=0.01,
        help="单笔最大亏损占资金比例，默认 0.01（百分之一）",
    )

    pc = sub.add_parser(
        "calibrate",
        help="条件单 walk-forward 历史校准：触达/先止盈/先止损后验（研究用）",
    )
    _add_kline_fetch_args(pc)
    _add_channel_args(pc, default="vwreg,donchian")
    pc.add_argument("--validity-days", type=int, default=5, help="有效期交易日，默认 5")
    pc.add_argument(
        "--scan",
        action="store_true",
        help="扫描 channel_window×σ 稳定性",
    )
    pc.add_argument("--json", action="store_true", help="输出 JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 两个专用画图工具也可经 emquote 子命令转发，便于统一发现入口。
    if argv and argv[0] == "plot-channel":
        return main_channel(argv[1:])
    if argv and argv[0] == "plot-pv":
        return main_pv(argv[1:])
    if argv and argv[0] == "plot-width":
        return main_width(argv[1:])
    if argv and argv[0] == "plot-ke":
        return main_ke(argv[1:])
    if argv and argv[0] == "plot-kline":
        return main_kline(argv[1:])
    if argv and argv[0] == "plot-daily":
        return main_daily(argv[1:])
    if argv and argv[0] == "plot-intraday":
        return main_intraday(argv[1:])

    args = build_parser().parse_args(argv)
    client = EastMoneyClient(timeout=args.timeout, retries=args.retries)
    try:
        if args.cmd == "quote":
            q = client.quote(args.symbol)
            if args.json:
                _emit_json({k: v for k, v in q.items() if k != "raw"})
            else:
                _print_quote(q)
            return 0

        if args.cmd == "kline":
            k = _fetch_kline(client, args)
            if args.csv:
                _emit_csv(k["bars"])
            elif args.json:
                _emit_json(k)
            else:
                _print_kline(k)
            return 0

        if args.cmd == "plot":
            k = _fetch_kline(client, args)
            kinds = parse_channels(args.channel)
            path = plot_close_curve(
                k,
                args.output,
                channel=args.channel,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                show_levels=not args.no_levels,
                full_range=not args.single_window,
            )
            print(f"已保存: {path}")
            print(
                f"{k.get('name')} {k.get('symbol')}  {k.get('interval')}  "
                f"bars={len(k['bars'])}  channel={','.join(kinds)}"
            )
            if kinds != ["none"]:
                report = suggest_condition_orders(
                    k,
                    channels=kinds,
                    channel_window=args.channel_window,
                    channel_width=args.channel_width,
                    full_range=not args.single_window,
                )
                print()
                print_condition_levels(report)
            return 0

        if args.cmd == "levels":
            k = _fetch_kline(client, args)
            kinds = parse_channels(args.channel)
            report = suggest_condition_orders(
                k,
                channels=kinds,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                full_range=not args.single_window,
            )
            if args.json:
                _emit_json(report)
            else:
                print_condition_levels(report)
            return 0

        if args.cmd == "plan":
            k = _fetch_kline(client, args)
            kinds = parse_channels(args.channel)
            plan = build_condition_plan(
                k,
                channels=kinds,
                channel_window=args.channel_window,
                channel_width=args.channel_width,
                full_range=not args.single_window,
                run_calibration=not bool(getattr(args, "no_calibrate", False)),
                with_param_scan=bool(getattr(args, "scan", False)),
                capital=getattr(args, "capital", None),
                risk_pct=float(getattr(args, "risk_pct", 0.01) or 0.01),
            )
            if args.json:
                # JSON 里去掉嵌套 levels 的冗余大字段可保留；便于 AI
                _emit_json(plan)
            else:
                print_condition_plan(plan)
            return 0

        if args.cmd == "calibrate":
            k = _fetch_kline(client, args)
            kinds = parse_channels(args.channel)
            window = args.channel_window if args.channel_window > 0 else 96
            report = calibrate_multi(
                k,
                kinds=[c for c in kinds if c != "none"],
                channel_window=window,
                channel_width=args.channel_width,
                validity_days=int(args.validity_days),
                with_scan=bool(getattr(args, "scan", False)),
            )
            if args.json:
                _emit_json(report)
            else:
                print("条件单历史校准（walk-forward · 研究用）")
                print(
                    f"标的: {report.get('name')} {report.get('symbol')}  "
                    f"周期: {report.get('interval')}  建议主通道: {report.get('suggested_primary')}"
                )
                print(report.get("note"))
                for kind, item in (report.get("by_kind") or {}).items():
                    print()
                    print(f"【{kind}】")
                    if not item.get("ok"):
                        print(f"  跳过: {item.get('reason')}")
                        continue
                    rates = item.get("rates") or {}
                    eb = item.get("empirical_bayes") or {}
                    print(
                        f"  决策点: {item.get('decisions')}  质量: {item.get('quality')}  "
                        f"EB: {eb.get('mode')}"
                    )
                    print(
                        f"  成交后验: {rates.get('fill', {}).get('mean')}  "
                        f"先止盈|成交: {rates.get('tp_given_fill', {}).get('mean')}  "
                        f"CI80={rates.get('tp_given_fill', {}).get('ci80')}  "
                        f"先止损|成交: {rates.get('stop_given_fill', {}).get('mean')}"
                    )
                    print(f"  counts: {item.get('counts')}")
                    print(f"  → {item.get('hint')}")
                scans = report.get("parameter_scan") or {}
                for kind, sc in scans.items():
                    print()
                    print(f"【参数扫描 {kind}】稳定性={sc.get('stability')}  "
                          f"tp={sc.get('tp_mean_avg')}±{sc.get('tp_mean_std')}")
                    print(f"  {sc.get('note')}")
                    if sc.get("best"):
                        b = sc["best"]
                        print(
                            f"  较优网格: window={b.get('window')} σ={b.get('width')} "
                            f"edge={b.get('edge')}"
                        )
                print()
                print("声明: 单票短样本回放，不是未来胜率。")
            return 0

    except (QuoteError, ValueError, RuntimeError) as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
