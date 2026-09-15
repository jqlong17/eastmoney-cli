"""emquote 命令行入口。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from .client import EastMoneyClient, QuoteError
from .plotting import plot_close_curve


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


def _emit_json(data: dict[str, Any]) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="emquote",
        description="东方财富公开行情只读 CLI：报价 / K 线 / 收盘价曲线。非交易接口。",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="单次请求超时秒数")
    p.add_argument("--retries", type=int, default=3, help="每主机重试次数")
    sub = p.add_subparsers(dest="cmd", required=True)

    pq = sub.add_parser("quote", help="拉取实时报价")
    pq.add_argument("symbol", help="如 603606.SH / 002223.SZ / 603606")
    pq.add_argument("--json", action="store_true", help="输出 JSON")

    pk = sub.add_parser("kline", help="拉取 K 线 / 成交价序列")
    pk.add_argument("symbol", nargs="?", default=None, help="股票代码；使用 --from-json 时可省略")
    pk.add_argument("-i", "--interval", default="5m", help="1m/5m/15m/30m/60m/1d/1w/1mo")
    pk.add_argument("--days", type=int, default=10, help="最近 N 个交易日，默认 10")
    pk.add_argument("--bars", type=int, default=None, help="只保留最近 N 根（在 --days 之后再截）")
    pk.add_argument("--adjust", choices=["none", "qfq", "hfq"], default="none", help="复权")
    pk.add_argument("--from-json", dest="from_json", help="离线读取东财原始 JSON")
    pk.add_argument("--json", action="store_true", help="输出 JSON")
    pk.add_argument("--csv", action="store_true", help="输出 CSV 到 stdout")

    pp = sub.add_parser("plot", help="绘制收盘价曲线并保存 PNG")
    pp.add_argument("symbol", nargs="?", default=None, help="股票代码；使用 --from-json 时可省略")
    pp.add_argument("-i", "--interval", default="5m", help="周期，默认 5m")
    pp.add_argument("--days", type=int, default=10, help="最近 N 个交易日，默认 10")
    pp.add_argument("--bars", type=int, default=None, help="只保留最近 N 根")
    pp.add_argument("--adjust", choices=["none", "qfq", "hfq"], default="none", help="复权")
    pp.add_argument("-o", "--output", default="emquote-chart.png", help="输出 PNG 路径")
    pp.add_argument("--from-json", dest="from_json", help="离线读取东财原始 JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = EastMoneyClient(timeout=args.timeout, retries=args.retries)
    try:
        if args.cmd == "quote":
            q = client.quote(args.symbol)
            if args.json:
                out = {k: v for k, v in q.items() if k != "raw"}
                _emit_json(out)
            else:
                _print_quote(q)
            return 0

        if args.cmd in {"kline", "plot"}:
            if args.from_json:
                k = _load_kline_json(args.from_json, args.days, args.bars)
            else:
                if not args.symbol:
                    raise QuoteError("请提供股票代码，或使用 --from-json")
                k = client.kline(
                    args.symbol,
                    interval=args.interval,
                    days=args.days,
                    bars=args.bars,
                    adjust=args.adjust,
                )
            if args.cmd == "kline":
                if args.csv:
                    _emit_csv(k["bars"])
                elif args.json:
                    _emit_json(k)
                else:
                    _print_kline(k)
                return 0

            path = plot_close_curve(k, args.output)
            print(f"已保存: {path}")
            print(f"{k.get('name')} {k.get('symbol')}  {k.get('interval')}  bars={len(k['bars'])}")
            return 0
    except QuoteError as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
