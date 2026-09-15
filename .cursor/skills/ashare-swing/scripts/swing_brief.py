#!/usr/bin/env python3
"""上班族波段条件单：报价 + 主图三件套 + 标准化 plan。

默认主图：日线 / 5m K 线 / 价量通道。
分时为可选辅图（--with-intraday），不作设单主依据。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _which(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"找不到命令 {name}。请先: pip install -e '.[plot]'")
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def _short(text: str, n: int = 220) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 3] + "..."


def _run_json(cmd: list[str]) -> dict:
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> {_short(proc.stderr or proc.stdout)}")
    text = (proc.stdout or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(f"未得到 JSON: {' '.join(cmd)} :: {_short(text)}")
    return json.loads(text[start : end + 1])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="波段条件单简报：报价 + 日线/K线/价量通道 + plan（分时可选）"
    )
    p.add_argument("symbol", help="如 603606.SH")
    p.add_argument("-o", "--outdir", default=None, help="输出目录，默认 /tmp/swing-<code>")
    p.add_argument("--days", type=int, default=10, help="5m/通道交易日数，默认 10")
    p.add_argument("--days-daily", type=int, default=120, help="日线交易日数，默认 120")
    p.add_argument("--demo", action="store_true", help="强制离线样例画图")
    p.add_argument("--with-intraday", action="store_true", help="额外画分时辅图（非设单主依据）")
    p.add_argument("--refresh", action="store_true", help="忽略 K 线缓存强制重拉")
    args = p.parse_args(argv)

    symbol = args.symbol.strip().upper()
    code = symbol.split(".")[0]
    outdir = Path(args.outdir or f"/tmp/swing-{code}").expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    root = _repo_root()
    sample_5m = root / "examples" / "sample-603606-5m.json"
    sample_1d = root / "examples" / "sample-603606-1d.json"

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
        "positioning": "上班族波段（几天到几周）+ 条件单代替盯盘；不做超短/打板",
        "charts": {},
        "errors": [],
    }

    try:
        summary["quote"] = _run_json([emquote, "quote", symbol, "--json"])
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"quote: {exc}")
        summary["quote"] = None

    def chart(kind: str, tool: str, outfile: str, *, daily: bool = False) -> None:
        out = str(outdir / outfile)
        if args.demo:
            src = sample_1d if daily else sample_5m
            cmd = [tool, "--from-json", str(src), "-o", out, "--json"]
        else:
            cmd = [tool, symbol, "-o", out, "--json"]
            if daily:
                cmd[2:2] = ["--days", str(args.days_daily)]
            elif kind != "intraday":
                cmd[2:2] = ["--days", str(args.days)]
            if args.refresh:
                cmd.append("--refresh")
        try:
            summary["charts"][kind] = _run_json(cmd)
        except Exception as exc:  # noqa: BLE001
            if not args.demo:
                src = sample_1d if daily else sample_5m
                if src.exists():
                    try:
                        data = _run_json([tool, "--from-json", str(src), "-o", out, "--json"])
                        data["_fallback"] = f"from-json:{src.name}"
                        summary["charts"][kind] = data
                        summary["errors"].append(f"{kind}: live failed, used offline {src.name}")
                        return
                    except Exception as exc2:  # noqa: BLE001
                        summary["errors"].append(f"{kind}: {exc} | fallback: {exc2}")
                        summary["charts"][kind] = None
                        return
            summary["errors"].append(f"{kind}: {exc}")
            summary["charts"][kind] = None

    chart("daily", tools["daily"], f"{code}-daily.png", daily=True)
    chart("kline", tools["kline"], f"{code}-kline.png")
    chart("pv", tools["pv"], f"{code}-pv.png")
    if args.with_intraday:
        chart("intraday", tools["intraday"], f"{code}-intraday.png")

    def levels_or_plan(subcommand: str) -> dict | None:
        if args.demo:
            cmd = [
                emquote,
                subcommand,
                "--from-json",
                str(sample_5m),
                "-i",
                "5m",
                "--days",
                str(args.days),
                "--channel",
                "vwreg,donchian",
                "--json",
            ]
        else:
            cmd = [
                emquote,
                subcommand,
                symbol,
                "-i",
                "5m",
                "--days",
                str(args.days),
                "--channel",
                "vwreg,donchian",
                "--json",
            ]
            if args.refresh:
                cmd.append("--refresh")
        try:
            return _run_json(cmd)
        except Exception as exc:  # noqa: BLE001
            if not args.demo and sample_5m.exists():
                try:
                    data = _run_json(
                        [
                            emquote,
                            subcommand,
                            "--from-json",
                            str(sample_5m),
                            "-i",
                            "5m",
                            "--days",
                            str(args.days),
                            "--channel",
                            "vwreg,donchian",
                            "--json",
                        ]
                    )
                    summary["errors"].append(
                        f"{subcommand}: live failed, used offline {sample_5m.name}"
                    )
                    return data
                except Exception as exc2:  # noqa: BLE001
                    summary["errors"].append(f"{subcommand}: {exc} | fallback: {exc2}")
                    return None
            summary["errors"].append(f"{subcommand}: {exc}")
            return None

    summary["levels"] = levels_or_plan("levels")
    summary["plan"] = levels_or_plan("plan")

    out_json = outdir / f"{code}-brief.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n# brief written: {out_json}", file=sys.stderr)
    ok = bool(summary.get("plan") or summary.get("quote") or any(summary["charts"].values()))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
