"""条件单方案滚动日记：时间戳快照，供日后复盘对照现价。

只存研究备注，不下单。默认目录 ~/.local/share/emquote/journal/
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .symbols import parse_symbol
from .touch import simulate_touch


CST = timezone(timedelta(hours=8))
JOURNAL_KIND = "emquote.journal.v1"
HEAVY_PLAN_KEYS = ("calibration_detail", "levels_report")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def default_journal_dir() -> Path:
    override = os.environ.get("EMQUOTE_JOURNAL_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "emquote" / "journal"
    return Path.home() / ".local" / "share" / "emquote" / "journal"


def _safe_token(text: str) -> str:
    return _SAFE.sub("_", text.strip()) or "unknown"


def normalize_symbol(symbol: str) -> str:
    _, code, market = parse_symbol(symbol)
    return f"{code}.{market}"


def _now_cst() -> datetime:
    return datetime.now(tz=CST)


def snapshot_stamp(when: datetime | None = None) -> str:
    dt = when or _now_cst()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST).strftime("%Y%m%d-%H%M%S")


def snapshot_path(symbol: str, stamp: str, *, root: Path | None = None) -> Path:
    return (root or default_journal_dir()) / _safe_token(normalize_symbol(symbol)) / f"{stamp}.json"


def slim_plan(plan: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    if full:
        return dict(plan)
    out = {k: v for k, v in plan.items() if k not in HEAVY_PLAN_KEYS}
    return out


def forecast_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    bz = plan.get("buy_zone") or {}
    tp = plan.get("take_profit") or {}
    sl = plan.get("stop_loss") or {}
    conc = (plan.get("sections") or {}).get("conclusion") or {}
    data = (plan.get("sections") or {}).get("data_boundary") or {}
    width = (data.get("width") or plan.get("width") or {})
    energy = (data.get("energy") or plan.get("energy") or {})
    validity = plan.get("validity") or {}
    return {
        "last_close": plan.get("last_close"),
        "last_time": plan.get("last_time"),
        "primary_channel": plan.get("primary_channel"),
        "primary_style": plan.get("primary_style"),
        "hangable": conc.get("hangable"),
        "summary": conc.get("summary"),
        "buy_low": bz.get("low"),
        "buy_high": bz.get("high"),
        "buy_trigger": bz.get("trigger"),
        "sell": tp.get("trigger"),
        "stop": sl.get("trigger"),
        "validity_days": validity.get("suggested_trading_days"),
        "width_rank": width.get("rank"),
        "width_label": width.get("label"),
        "energy_rank": energy.get("rank"),
        "energy_label": energy.get("label"),
        "interval": plan.get("interval"),
        "adjust": plan.get("adjust"),
    }


def build_snapshot(
    *,
    plan: dict[str, Any],
    quote: dict[str, Any] | None = None,
    note: str = "",
    full_plan: bool = False,
    saved_at: datetime | None = None,
) -> dict[str, Any]:
    dt = saved_at or _now_cst()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    symbol = str(plan.get("symbol") or (quote or {}).get("symbol") or "")
    if symbol:
        symbol = normalize_symbol(symbol)
    stamp = snapshot_stamp(dt)
    slim = slim_plan(plan, full=full_plan)
    quote_slim = None
    if quote:
        quote_slim = {k: v for k, v in quote.items() if k != "raw"}
    return {
        "kind": JOURNAL_KIND,
        "id": f"{symbol}@{stamp}" if symbol else stamp,
        "stamp": stamp,
        "saved_at": dt.astimezone(CST).isoformat(timespec="seconds"),
        "note": note or "",
        "symbol": symbol,
        "code": plan.get("code") or (quote or {}).get("code"),
        "name": plan.get("name") or (quote or {}).get("name"),
        "quote": quote_slim,
        "forecast": forecast_from_plan(plan),
        "plan": slim,
        "disclaimer": "研究快照，非交易指令；日后用 journal diff 对照现价。",
    }


def save_snapshot(snapshot: dict[str, Any], *, root: Path | None = None) -> Path:
    symbol = str(snapshot.get("symbol") or "unknown")
    stamp = str(snapshot.get("stamp") or snapshot_stamp())
    path = snapshot_path(symbol, stamp, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_snapshots(symbol: str | None = None, *, root: Path | None = None) -> list[dict[str, Any]]:
    base = root or default_journal_dir()
    if not base.exists():
        return []
    if symbol:
        dirs = [base / _safe_token(normalize_symbol(symbol))]
    else:
        dirs = sorted(p for p in base.iterdir() if p.is_dir())
    rows: list[dict[str, Any]] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for path in sorted(d.glob("*.json"), reverse=True):
            try:
                snap = load_snapshot(path)
            except (OSError, json.JSONDecodeError):
                continue
            fc = snap.get("forecast") or {}
            rows.append(
                {
                    "id": snap.get("id") or f"{d.name}@{path.stem}",
                    "path": str(path),
                    "stamp": snap.get("stamp") or path.stem,
                    "saved_at": snap.get("saved_at"),
                    "symbol": snap.get("symbol") or d.name,
                    "name": snap.get("name"),
                    "last_close": fc.get("last_close"),
                    "buy_low": fc.get("buy_low"),
                    "buy_high": fc.get("buy_high"),
                    "sell": fc.get("sell"),
                    "stop": fc.get("stop"),
                    "hangable": fc.get("hangable"),
                    "note": snap.get("note") or "",
                }
            )
    rows.sort(key=lambda r: str(r.get("stamp") or ""), reverse=True)
    return rows


def resolve_snapshot(
    symbol: str,
    stamp: str | None = None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    rows = list_snapshots(symbol, root=root)
    if not rows:
        raise FileNotFoundError(f"没有找到 {normalize_symbol(symbol)} 的日记快照")
    if not stamp:
        path = Path(rows[0]["path"])
        return load_snapshot(path)
    want = stamp.strip()
    if want.endswith(".json"):
        want = Path(want).stem
    if "@" in want:
        want = want.split("@", 1)[1]
    for row in rows:
        if row["stamp"] == want or row["id"] == stamp or Path(row["path"]).stem == want:
            return load_snapshot(Path(row["path"]))
    raise FileNotFoundError(f"没有找到快照 {stamp}（标的 {normalize_symbol(symbol)}）")


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(numer: float, denom: float) -> float | None:
    if not denom:
        return None
    return round(100.0 * numer / denom, 2)


def _parse_time(text: Any) -> datetime | None:
    if not text:
        return None
    raw = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw[:19] if "T" in raw or " " in raw else raw[:10], fmt)
            return dt.replace(tzinfo=CST)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt
    except ValueError:
        return None


def classify_price(price: float, forecast: dict[str, Any]) -> dict[str, Any]:
    buy_low = _f(forecast.get("buy_low"))
    buy_high = _f(forecast.get("buy_high"))
    sell = _f(forecast.get("sell"))
    stop = _f(forecast.get("stop"))
    in_buy = buy_low is not None and buy_high is not None and buy_low <= price <= buy_high
    above_tp = sell is not None and price >= sell
    below_stop = stop is not None and price <= stop
    if above_tp:
        vs = "at_or_above_take_profit"
        vs_zh = "现价已到/越过止盈"
    elif below_stop:
        vs = "at_or_below_stop"
        vs_zh = "现价已到/跌破止损"
    elif in_buy:
        vs = "inside_buy_zone"
        vs_zh = "现价落在买入区"
    elif buy_high is not None and price > buy_high:
        vs = "above_buy_zone"
        vs_zh = "现价在买入区上方"
    elif buy_low is not None and price < buy_low:
        vs = "below_buy_zone"
        vs_zh = "现价在买入区下方"
    else:
        vs = "unknown"
        vs_zh = "轨位不全，无法对照"

    def dist(level: float | None, label: str) -> dict[str, Any] | None:
        if level is None:
            return None
        delta = round(price - level, 4)
        return {
            "level": level,
            "delta": delta,
            "pct": _pct(delta, level),
            "label": label,
        }

    return {
        "vs_rails": vs,
        "vs_rails_zh": vs_zh,
        "in_buy_zone": in_buy,
        "at_or_above_take_profit": above_tp,
        "at_or_below_stop": below_stop,
        "to_buy_low": dist(buy_low, "买入区下沿"),
        "to_buy_high": dist(buy_high, "买入区上沿"),
        "to_sell": dist(sell, "止盈"),
        "to_stop": dist(stop, "止损"),
    }


def replay_path(bars: list[dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """用快照之后的 K 线 high/low，按共享触达规则复盘是否进区/先止盈/先止损。"""
    fc = snapshot.get("forecast") or {}
    buy_low, buy_high = _f(fc.get("buy_low")), _f(fc.get("buy_high"))
    sell, stop = _f(fc.get("sell")), _f(fc.get("stop"))
    if None in (buy_low, buy_high, sell, stop):
        return None
    saved = _parse_time(snapshot.get("saved_at")) or _parse_time(fc.get("last_time"))
    after = []
    for b in bars:
        t = _parse_time(b.get("time"))
        if saved is None or t is None or t > saved:
            after.append(b)
    if not after:
        return {"ok": False, "reason": "快照之后没有新 K 线", "outcome": None}
    # 在序列前垫一根决策 bar，simulate_touch 从 t+1 开始扫。
    dummy = {
        "time": fc.get("last_time") or snapshot.get("saved_at"),
        "open": fc.get("last_close"),
        "high": fc.get("last_close"),
        "low": fc.get("last_close"),
        "close": fc.get("last_close"),
        "volume": 0,
    }
    seq = [dummy, *after]
    rails = {
        "buy_low": buy_low,
        "buy_high": buy_high,
        "sell": sell,
        "stop": stop,
    }
    outcome = simulate_touch(seq, 0, rails, horizon=len(after))
    return {
        "ok": True,
        "engine": "shared_touch",
        "bars_after": len(after),
        "first_bar": after[0].get("time"),
        "last_bar": after[-1].get("time"),
        "outcome": outcome,
        "outcome_zh": {
            "tp_first": "进区后先触止盈",
            "stop_first": "进区后先触止损",
            "expire_unfilled": "窗口内未进买入区",
            "timeout_after_fill": "进区后有效期内未触止盈/止损",
        }.get(outcome, outcome),
    }


def diff_snapshot(
    snapshot: dict[str, Any],
    *,
    last: float,
    last_time: str | None = None,
    bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fc = snapshot.get("forecast") or {}
    snap_px = _f(fc.get("last_close"))
    change = None if snap_px is None else round(last - snap_px, 4)
    change_pct = None if snap_px is None else _pct(last - snap_px, snap_px)
    saved = _parse_time(snapshot.get("saved_at")) or _parse_time(fc.get("last_time"))
    now = _parse_time(last_time) or _now_cst()
    calendar_days = None
    if saved is not None:
        calendar_days = max(0, (now.date() - saved.date()).days)
    validity = fc.get("validity_days")
    expired = None
    if calendar_days is not None and validity:
        # 自然日粗算：有效期交易日 × 1.6 约等于日历窗。精确交易日需交易日历。
        expired = calendar_days > int(validity) * 2
    classified = classify_price(last, fc)
    path = replay_path(bars, snapshot) if bars else None
    return {
        "kind": "emquote.journal.diff.v1",
        "snapshot_id": snapshot.get("id"),
        "symbol": snapshot.get("symbol"),
        "name": snapshot.get("name"),
        "saved_at": snapshot.get("saved_at"),
        "snapshot_last": snap_px,
        "snapshot_last_time": fc.get("last_time"),
        "current_last": last,
        "current_last_time": last_time,
        "change": change,
        "change_pct": change_pct,
        "calendar_days": calendar_days,
        "validity_days": validity,
        "validity_likely_expired": expired,
        "forecast": fc,
        "vs_current": classified,
        "path": path,
        "disclaimer": "对照现价与当时轨位，不是成交回报；不下单。",
    }


def print_snapshot_saved(path: Path, snapshot: dict[str, Any]) -> None:
    fc = snapshot.get("forecast") or {}
    print("已保存条件单日记快照（研究用，不下单）")
    print(f"  id: {snapshot.get('id')}")
    print(f"  文件: {path}")
    print(
        f"  {snapshot.get('name')} {snapshot.get('symbol')}  "
        f"现价 {fc.get('last_close')}  时间 {fc.get('last_time')}"
    )
    print(
        f"  买入区 {fc.get('buy_low')}～{fc.get('buy_high')}  "
        f"止盈 {fc.get('sell')}  止损 {fc.get('stop')}"
    )
    if snapshot.get("note"):
        print(f"  备注: {snapshot['note']}")


def print_snapshot_list(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("（没有日记快照）")
        print("用 emquote journal save <代码> 先存一版 plan。")
        return
    print(f"{'时间戳':<16} {'代码':<12} {'现价':>8} {'买入区':<21} {'止盈':>8} {'止损':>8} 可挂")
    for row in rows:
        zone = ""
        if row.get("buy_low") is not None and row.get("buy_high") is not None:
            zone = f"{row['buy_low']}～{row['buy_high']}"
        hang = "是" if row.get("hangable") else ("否" if row.get("hangable") is False else "-")
        last = row.get("last_close")
        last_s = f"{last:.2f}" if isinstance(last, (int, float)) else "-"
        sell = row.get("sell")
        stop = row.get("stop")
        sell_s = f"{sell:.2f}" if isinstance(sell, (int, float)) else "-"
        stop_s = f"{stop:.2f}" if isinstance(stop, (int, float)) else "-"
        print(
            f"{str(row.get('stamp') or ''):<16} {str(row.get('symbol') or ''):<12} "
            f"{last_s:>8} {zone:<21} {sell_s:>8} {stop_s:>8} {hang}"
        )


def print_diff(report: dict[str, Any]) -> None:
    print("条件单日记对照（研究用，不下单）")
    print(
        f"标的: {report.get('name')} {report.get('symbol')}  "
        f"快照 {report.get('snapshot_id')}"
    )
    print(f"当时: {report.get('snapshot_last')}  @ {report.get('snapshot_last_time')}")
    print(f"现在: {report.get('current_last')}  @ {report.get('current_last_time')}")
    chg = report.get("change")
    pct = report.get("change_pct")
    if chg is not None:
        sign = "+" if chg >= 0 else ""
        print(f"涨跌: {sign}{chg}  ({sign}{pct}%)  间隔约 {report.get('calendar_days')} 个自然日")
    vs = report.get("vs_current") or {}
    print(f"对照轨位: {vs.get('vs_rails_zh')}")
    fc = report.get("forecast") or {}
    print(
        f"  买入区 {fc.get('buy_low')}～{fc.get('buy_high')}  "
        f"止盈 {fc.get('sell')}  止损 {fc.get('stop')}"
    )
    for key in ("to_buy_high", "to_sell", "to_stop"):
        item = vs.get(key) or {}
        if not item:
            continue
        delta = item.get("delta")
        pct_i = item.get("pct")
        print(f"  距{item.get('label')}: {delta} ({pct_i}%)")
    if report.get("validity_days"):
        exp = report.get("validity_likely_expired")
        hint = "可能已过建议有效期，需重挂复盘" if exp else "建议有效期窗口多半仍在"
        print(f"有效期: 当时建议 {report.get('validity_days')} 个交易日；{hint}")
    path = report.get("path") or {}
    if path.get("ok"):
        print(
            f"路径复盘: {path.get('outcome_zh')}（{path.get('outcome')}，"
            f"{path.get('bars_after')} 根快照后 K 线）"
        )
    elif path and path.get("reason"):
        print(f"路径复盘: {path.get('reason')}")
    print(f"说明: {report.get('disclaimer')}")
