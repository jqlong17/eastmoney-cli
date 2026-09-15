#!/usr/bin/env python3
"""一键：报价 + 中短线四图 + 条件单参考价 JSON。

用法:
  python .cursor/skills/ashare-swing/scripts/swing_brief.py 603606.SH -o /tmp/swing-603606
  # K 线接口不通时用离线样例：
  python .cursor/skills/ashare-swing/scripts/swing_brief.py 603606.SH --demo -o /tmp/swing-demo
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    # scripts/ -> ashare-swing/ -> skills/ -> .cursor/ -> repo root
    return Path(__file__).resolve().parents[4]


def _which(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(
            f"找不到命令 {name}。请先在仓库根目录: pip install -e '.[plot]'"
        )
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def _short_err(text: str, limit: int = 240) -> str:
    t = " ".join((text or "").strip().split())
    return t if len(t) <= limit else t[: limit - 3] + "..."


def _run_json(cmd: list[str]) -> dict:
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)} -> {_short_err(proc.stderr or proc.stdout)}"
        )
    text = (proc.stdout or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(f"未得到 JSON: {' '.join(cmd)} :: {_short_err(text)}")
    return json.loads(text[start : end + 1])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="A 股中短线看盘：报价+四图+条件单价")
    p.add_argument("symbol", help="如 603606.SH")
    p.add_argument("-o", "--outdir", default=None, help="输出目录，默认 /tmp/swing-<code>")
    p.add_argument("--days-intraday", type=int, default=10, help="5m/通道图交易日数")
    p.add_argument("--days-daily", type=int, default=120, help="日线交易日数")
    p.add_argument(
        "--demo",
        action="store_true",
        help="强制用 examples 离线 JSON 画图（验证链路/节点挂掉时）",
    )
    p.add_argument("--from-json-5m", default=None, help="5m/分时/通道离线 JSON")
    p.add_argument("--from-json-1d", default=None, help="日线离线 JSON")
    args = p.parse_args(argv)

    symbol = args.symbol.strip().upper()
    code = symbol.split(".")[0]
    outdir = Path(args.outdir or f"/tmp/swing-{code}").expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    root = _repo_root()
    sample_5m = Path(args.from_json_5m) if args.from_json_5m else root / "examples" / "sample-603606-5m.json"
    sample_1d = Path(args.from_json_1d) if args.from_json_1d else root / "examples" / "sample-603606-1d.json"

    emquote = _which("emquote")
    tools = {
        "daily": _which("emplot-daily"),
        "kline": _which("emplot-kline"),
        "pv": _which("emplot-pv"),
        "intraday": _which("emplot-intraday"),
    }

    summary: dict = {
        "symbol": symbol,
        "outdir": str(outdir),
        "mode": "demo" if args.demo else "live",
        "charts": {},
        "errors": [],
    }

    try:
        summary["quote"] = _run_json([emquote, "quote", symbol, "--json"])
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"quote: {exc}")
        summary["quote"] = None

    def chart_cmd(kind: str, tool: str, out_name: str, *, daily: bool = False) -> list[str]:
        out = str(outdir / out_name)
        if args.demo:
            src = sample_1d if daily else sample_5m
            return [tool, "--from-json", str(src), "-o", out, "--json"]
        if daily:
            return [tool, symbol, "--days", str(args.days_daily), "-o", out, "--json"]
        if kind == "intraday":
            return [tool, symbol, "-o", out, "--json"]
        return [tool, symbol, "--days", str(args.days_intraday), "-o", out, "--json"]

    jobs = [
        ("daily", tools["daily"], f"{code}-daily.png", True),
        ("kline", tools["kline"], f"{code}-kline.png", False),
        ("pv", tools["pv"], f"{code}-pv.png", False),
        ("intraday", tools["intraday"], f"{code}-intraday.png", False),
    ]

    for kind, tool, name, daily in jobs:
        cmd = chart_cmd(kind, tool, name, daily=daily)
        try:
            summary["charts"][kind] = _run_json(cmd)
        except Exception as exc:  # noqa: BLE001
            # live 失败则自动尝试离线样例一次
            if not args.demo and (sample_5m.exists() if not daily else sample_1d.exists()):
                src = sample_1d if daily else sample_5m
                fallback = [tool, "--from-json", str(src), "-o", str(outdir / name), "--json"]
                try:
                    data = _run_json(fallback)
                    data["_fallback"] = f"from-json:{src.name}"
                    summary["charts"][kind] = data
                    summary["errors"].append(f"{kind}: live failed, used offline {src.name}")
                    continue
                except Exception as exc2:  # noqa: BLE001
                    summary["errors"].append(f"{kind}: {exc} | fallback: {exc2}")
                    summary["charts"][kind] = None
                    continue
            summary["errors"].append(f"{kind}: {exc}")
            summary["charts"][kind] = None

    # levels
    try:
        if args.demo:
            levels_cmd = [
                emquote,
                "levels",
                "--from-json",
                str(sample_5m),
                "-i",
                "5m",
                "--days",
                str(args.days_intraday),
                "--channel",
                "vwreg,donchian",
                "--json",
            ]
        else:
            levels_cmd = [
                emquote,
                "levels",
                symbol,
                "-i",
                "5m",
                "--days",
                str(args.days_intraday),
                "--channel",
                "vwreg,donchian",
                "--json",
            ]
        summary["levels"] = _run_json(levels_cmd)
    except Exception as exc:  # noqa: BLE001
        if not args.demo and sample_5m.exists():
            try:
                summary["levels"] = _run_json(
                    [
                        emquote,
                        "levels",
                        "--from-json",
                        str(sample_5m),
                        "-i",
                        "5m",
                        "--days",
                        str(args.days_intraday),
                        "--channel",
                        "vwreg,donchian",
                        "--json",
                    ]
                )
                summary["errors"].append(f"levels: live failed, used offline {sample_5m.name}")
            except Exception as exc2:  # noqa: BLE001
                summary["errors"].append(f"levels: {exc} | fallback: {exc2}")
                summary["levels"] = None
        else:
            summary["errors"].append(f"levels: {exc}")
            summary["levels"] = None

    out_json = outdir / f"{code}-brief.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n# brief written: {out_json}", file=sys.stderr)
    # soft success if quote ok and at least one chart
    charts_ok = any(v for v in summary["charts"].values())
    return 0 if summary.get("quote") or charts_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
