"""轻量本地 K 线缓存：网络失败时可回退最近成功快照。

默认目录：~/.cache/emquote/kline/
只缓存公开行情，不含任何密钥。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    override = os.environ.get("EMQUOTE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "emquote" / "kline"
    return Path.home() / ".cache" / "emquote" / "kline"


def cache_ttl_seconds(interval: str) -> int:
    key = (interval or "5m").lower()
    if key in {"1d", "day", "1w", "week", "1mo", "month"}:
        return 6 * 3600
    if key in {"60m", "30m", "15m"}:
        return 30 * 60
    if key in {"5m"}:
        return 15 * 60
    return 5 * 60


def _safe_token(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)


def cache_key(symbol: str, *, interval: str, adjust: str, days: int | None, bars: int | None) -> str:
    return _safe_token(
        f"{symbol}_{interval}_{adjust}_d{days if days is not None else 'all'}_b{bars if bars is not None else 'all'}"
    )


def cache_path(key: str, cache_dir: Path | None = None) -> Path:
    root = cache_dir or default_cache_dir()
    return root / f"{key}.json"


def write_kline_cache(key: str, payload: dict[str, Any], *, cache_dir: Path | None = None) -> Path:
    path = cache_path(key, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "cached_at": time.time(),
        "payload": payload,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def read_kline_cache(
    key: str,
    *,
    max_age: float | None,
    cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    path = cache_path(key, cache_dir)
    if not path.exists():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cached_at = float(envelope.get("cached_at") or 0)
    if max_age is not None and max_age >= 0 and (time.time() - cached_at) > max_age:
        return None
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or not payload.get("bars"):
        return None
    out = dict(payload)
    age = int(time.time() - cached_at)
    src = str(out.get("source") or "")
    out["source"] = f"cache:{path.name} ({age}s) <- {src}"
    out["cache"] = {"path": str(path), "age_seconds": age, "cached_at": cached_at}
    return out
