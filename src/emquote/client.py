"""东方财富网站公开 HTTP 行情接口封装（只读，非官方）。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .cache import (
    cache_key,
    cache_ttl_seconds,
    read_kline_cache,
    write_kline_cache,
)
from .symbols import SymbolError, parse_symbol

CST = timezone(timedelta(hours=8))

# 网站公开固定 ut，与 AKShare 相同；不是用户密钥。
PUBLIC_UT = "7eea3edcaed734bea9cbfc24409ed989"

QUOTE_URLS = (
    "https://push2.eastmoney.com/api/qt/stock/get",
    "https://push2delay.eastmoney.com/api/qt/stock/get",
)
KLINE_URLS = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://push2hisdelay.eastmoney.com/api/qt/stock/kline/get",
    "https://33.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://82.push2his.eastmoney.com/api/qt/stock/kline/get",
)

QUOTE_FIELDS = "f57,f58,f43,f44,f45,f46,f60,f169,f170,f47,f48,f86"

INTERVAL_KLT = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1d": "101",
    "day": "101",
    "1w": "102",
    "week": "102",
    "1mo": "103",
    "month": "103",
}

ADJUST_FQT = {
    "none": "0",
    "qfq": "1",
    "hfq": "2",
}


class QuoteError(RuntimeError):
    pass


def format_quote_time(value: Any) -> str:
    if value in (None, "", "-", 0, "0"):
        return ""
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, int):
        if value >= 10**12:
            value //= 1000
        if 10**9 <= value < 10**10:
            return datetime.fromtimestamp(value, tz=CST).strftime("%Y-%m-%d %H:%M:%S")
        text = str(value)
        if len(text) == 14:
            return datetime.strptime(text, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        if len(text) == 12:
            return datetime.strptime(text, "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _parse_kline_row(item: str) -> dict[str, Any]:
    parts = str(item).split(",")
    if len(parts) < 7:
        raise QuoteError(f"K 线字段不足: {item!r}")
    return {
        "time": parts[0],
        "open": float(parts[1]),
        "close": float(parts[2]),
        "high": float(parts[3]),
        "low": float(parts[4]),
        "volume": float(parts[5]),
        "amount": float(parts[6]),
    }


class EastMoneyClient:
    def __init__(self, timeout: float = 20.0, retries: int = 3, pause: float = 0.8) -> None:
        self.timeout = timeout
        self.retries = max(1, retries)
        self.pause = pause
        self.session = requests.Session()
        # 忽略环境代理，避免本机代理把东财历史节点掐断。
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0.0.0 Safari/537.36"
                ),
                "Referer": "https://quote.eastmoney.com/",
                "Accept": "application/json,text/plain,*/*",
            }
        )

    def _get_json(self, urls: tuple[str, ...], params: dict[str, str]) -> tuple[dict[str, Any], str]:
        errors: list[str] = []
        for url in urls:
            for attempt in range(self.retries):
                try:
                    resp = self.session.get(url, params=params, timeout=self.timeout)
                    if resp.status_code != 200:
                        errors.append(f"{url} HTTP {resp.status_code}")
                        time.sleep(self.pause * (attempt + 1))
                        continue
                    text = resp.content.decode("utf-8-sig", errors="replace").strip()
                    if not text.startswith("{"):
                        errors.append(f"{url} 非 JSON")
                        time.sleep(self.pause * (attempt + 1))
                        continue
                    payload = json.loads(text)
                    if not isinstance(payload, dict):
                        errors.append(f"{url} 响应不是对象")
                        continue
                    return payload, url
                except Exception as exc:  # noqa: BLE001 — 汇总后统一抛出
                    errors.append(f"{url} try{attempt + 1}: {exc}")
                    time.sleep(self.pause * (attempt + 1))
        raise QuoteError("请求失败:\n  " + "\n  ".join(errors))

    def quote(self, symbol: str) -> dict[str, Any]:
        try:
            secid, code, market = parse_symbol(symbol)
        except SymbolError as exc:
            raise QuoteError(str(exc)) from exc
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": QUOTE_FIELDS,
            "secid": secid,
        }
        payload, url = self._get_json(QUOTE_URLS, params)
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("f57"):
            raise QuoteError(f"无报价数据: {symbol}")
        return {
            "secid": secid,
            "symbol": f"{code}.{market}",
            "code": str(data.get("f57")),
            "name": str(data.get("f58") or ""),
            "last": data.get("f43"),
            "time": format_quote_time(data.get("f86")),
            "change": data.get("f169"),
            "pct": data.get("f170"),
            "open": data.get("f46"),
            "high": data.get("f44"),
            "low": data.get("f45"),
            "pre_close": data.get("f60"),
            "volume": data.get("f47"),
            "amount": data.get("f48"),
            "source": url,
            "raw": data,
        }

    def kline(
        self,
        symbol: str,
        *,
        interval: str = "5m",
        days: int | None = 10,
        bars: int | None = None,
        adjust: str = "none",
        end: str | None = None,
        use_cache: bool = True,
        refresh: bool = False,
        cache_ttl: int | None = None,
    ) -> dict[str, Any]:
        try:
            secid, code, market = parse_symbol(symbol)
        except SymbolError as exc:
            raise QuoteError(str(exc)) from exc
        key = interval.strip().lower()
        if key not in INTERVAL_KLT:
            raise QuoteError(
                f"不支持的周期 {interval!r}，可选: {', '.join(INTERVAL_KLT)}"
            )
        if adjust not in ADJUST_FQT:
            raise QuoteError("复权参数仅支持 none / qfq / hfq")

        cache_id = cache_key(symbol, interval=key, adjust=adjust, days=days, bars=bars)
        ttl = cache_ttl_seconds(key) if cache_ttl is None else cache_ttl
        if use_cache and not refresh:
            cached = read_kline_cache(cache_id, max_age=float(ttl))
            if cached is not None:
                return cached

        now = datetime.now(tz=CST)
        end_s = end or now.strftime("%Y%m%d")
        if days is not None and days > 0:
            # 多留日历日，覆盖周末/长假，再在客户端按交易日截取。
            pad = max(days * 3, days + 14)
            beg_s = (now - timedelta(days=pad)).strftime("%Y%m%d")
        else:
            beg_s = "0"

        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": PUBLIC_UT,
            "klt": INTERVAL_KLT[key],
            "fqt": ADJUST_FQT[adjust],
            "secid": secid,
            "beg": beg_s,
            "end": end_s,
        }
        try:
            payload, url = self._get_json(KLINE_URLS, params)
        except QuoteError:
            if use_cache:
                stale = read_kline_cache(cache_id, max_age=None)
                if stale is not None:
                    stale = dict(stale)
                    stale["source"] = f"cache-stale:{stale.get('source')}"
                    return stale
            raise
        data = payload.get("data") or {}
        raw = data.get("klines") or []
        rows = [_parse_kline_row(item) for item in raw]

        if days is not None and days > 0 and rows:
            dates = sorted({r["time"][:10] for r in rows})
            keep = set(dates[-days:])
            rows = [r for r in rows if r["time"][:10] in keep]
        if bars is not None and bars > 0:
            rows = rows[-bars:]

        result = {
            "secid": secid,
            "symbol": f"{code}.{market}",
            "code": str(data.get("code") or code),
            "name": str(data.get("name") or ""),
            "interval": key,
            "adjust": adjust,
            "source": url,
            "bars": rows,
        }
        if use_cache:
            try:
                write_kline_cache(cache_id, result)
            except OSError:
                pass
        return result

    @staticmethod
    def kline_from_payload(
        payload: dict[str, Any],
        *,
        days: int | None = None,
        bars: int | None = None,
    ) -> dict[str, Any]:
        """从已保存的东财 JSON（含 data.klines）解析，便于离线画图。"""
        data = payload.get("data") or {}
        raw = data.get("klines") or []
        rows = [_parse_kline_row(item) for item in raw]
        if days is not None and days > 0 and rows:
            dates = sorted({r["time"][:10] for r in rows})
            keep = set(dates[-days:])
            rows = [r for r in rows if r["time"][:10] in keep]
        if bars is not None and bars > 0:
            rows = rows[-bars:]
        code = str(data.get("code") or "")
        market = "SH" if data.get("market") == 1 else "SZ"
        return {
            "secid": f"{data.get('market', '')}.{code}",
            "symbol": f"{code}.{market}" if code else "",
            "code": code,
            "name": str(data.get("name") or ""),
            "interval": "from-json",
            "adjust": "unknown",
            "source": "local-json",
            "bars": rows,
        }
