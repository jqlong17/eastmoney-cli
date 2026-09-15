"""A 股代码解析：603606.SH / SH603606 / 603606 → secid。"""

from __future__ import annotations


class SymbolError(ValueError):
    pass


def parse_symbol(symbol: str) -> tuple[str, str, str]:
    """返回 (secid, code, market)。market 为 SH/SZ/BJ。"""
    raw = symbol.strip().upper().replace(" ", "")
    market = ""
    code = raw
    if "." in raw:
        left, right = raw.split(".", 1)
        if left in {"SH", "SZ", "BJ"} and right.isdigit():
            market, code = left, right
        elif right in {"SH", "SZ", "BJ"} and left.isdigit():
            code, market = left, right
        else:
            raise SymbolError(f"无法解析代码: {symbol}")
    elif raw.startswith(("SH", "SZ", "BJ")) and len(raw) == 8 and raw[2:].isdigit():
        market, code = raw[:2], raw[2:]
    if not code.isdigit() or len(code) != 6:
        raise SymbolError(f"需要 6 位 A 股代码: {symbol}")
    if not market:
        if code.startswith(("5", "6", "9")):
            market = "SH"
        elif code.startswith(("0", "1", "2", "3")):
            market = "SZ"
        elif code.startswith(("4", "8")):
            market = "BJ"
        else:
            raise SymbolError(f"无法从代码推断市场: {symbol}")
    market_id = "1" if market == "SH" else "0"
    return f"{market_id}.{code}", code, market
