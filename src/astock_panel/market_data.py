from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime


EASTMONEY_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
SINA_URL = "https://hq.sinajs.cn/list="
QUOTE_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f18,f20,f124"


class QuoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class StockQuote:
    secid: str
    code: str
    name: str
    price: float | None
    change_percent: float | None
    change_amount: float | None
    volume: float | None
    amount: float | None
    previous_close: float | None
    update_time: str


def normalize_symbol(symbol: str) -> str:
    value = symbol.strip()
    if not value:
        raise ValueError("股票代码不能为空")
    if "." in value:
        market, code = value.split(".", 1)
        if market not in {"0", "1"} or not code.isdigit():
            raise ValueError(f"不支持的代码格式: {symbol}")
        return f"{market}.{code.zfill(6)}"

    code = value.zfill(6)
    if not code.isdigit():
        raise ValueError(f"不支持的代码格式: {symbol}")

    if code.startswith(("5", "6", "9")) or code in {"000001", "000016", "000300", "000905", "000688"}:
        return f"1.{code}"
    return f"0.{code}"


def fetch_quotes(symbols: list[str], timeout: float = 8.0) -> list[StockQuote]:
    secids = [normalize_symbol(symbol) for symbol in symbols]
    if not secids:
        return []

    try:
        return _fetch_eastmoney(secids, timeout)
    except QuoteError:
        return _fetch_sina(secids, timeout)


def _fetch_eastmoney(secids: list[str], timeout: float) -> list[StockQuote]:

    query = urllib.parse.urlencode(
        {
            "fltt": "2",
            "secids": ",".join(secids),
            "fields": QUOTE_FIELDS,
        }
    )
    request = urllib.request.Request(
        f"{EASTMONEY_URL}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        },
    )

    last_error: OSError | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except OSError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.6)
                continue
            raise QuoteError(f"网络请求失败: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise QuoteError("行情返回内容无法解析") from exc
    else:
        raise QuoteError(f"网络请求失败: {last_error}")

    if payload.get("rc") != 0:
        raise QuoteError(f"行情接口返回异常: {payload.get('rc')}")

    rows = payload.get("data", {}).get("diff", [])
    quotes: list[StockQuote] = []
    for secid, row in zip(secids, rows):
        quotes.append(_row_to_quote(secid, row))
    return quotes


def _fetch_sina(secids: list[str], timeout: float) -> list[StockQuote]:
    sina_symbols = [_to_sina_symbol(secid) for secid in secids]
    request = urllib.request.Request(
        f"{SINA_URL}{','.join(sina_symbols)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content = response.read().decode("gb18030", errors="replace")
    except OSError as exc:
        raise QuoteError(f"网络请求失败: {exc}") from exc

    rows = {
        match.group("symbol"): match.group("payload").split(",")
        for match in re.finditer(r'var hq_str_(?P<symbol>\w+)="(?P<payload>.*?)";', content)
    }

    quotes: list[StockQuote] = []
    for secid, sina_symbol in zip(secids, sina_symbols):
        parts = rows.get(sina_symbol)
        if not parts or not parts[0]:
            continue
        quotes.append(_sina_row_to_quote(secid, parts))
    return quotes


def _to_sina_symbol(secid: str) -> str:
    market, code = secid.split(".", 1)
    prefix = "sh" if market == "1" else "sz"
    return f"{prefix}{code}"


def _sina_row_to_quote(secid: str, parts: list[str]) -> StockQuote:
    code = secid.split(".", 1)[1]
    previous_close = _number(parts[2] if len(parts) > 2 else None)
    price = _number(parts[3] if len(parts) > 3 else None)
    amount = _number(parts[9] if len(parts) > 9 else None)
    volume = _number(parts[8] if len(parts) > 8 else None)
    is_index = secid.startswith("1.000") or secid.startswith("0.399")
    if volume is not None and not is_index:
        volume = volume / 100

    change_amount: float | None = None
    change_percent: float | None = None
    if price is not None and previous_close not in (None, 0):
        change_amount = price - previous_close
        change_percent = change_amount / previous_close * 100

    date_part = parts[30] if len(parts) > 30 else ""
    time_part = parts[31] if len(parts) > 31 else ""
    update_time = time_part or time.strftime("%H:%M:%S")

    return StockQuote(
        secid=secid,
        code=code,
        name=parts[0],
        price=price,
        change_percent=change_percent,
        change_amount=change_amount,
        volume=volume,
        amount=amount,
        previous_close=previous_close,
        update_time=update_time if not date_part else update_time,
    )


def _number(value: object) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_quote(secid: str, row: dict) -> StockQuote:
    timestamp = _number(row.get("f124"))
    if timestamp:
        update_time = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
    else:
        update_time = time.strftime("%H:%M:%S")

    return StockQuote(
        secid=secid,
        code=str(row.get("f12", "")),
        name=str(row.get("f14", "")),
        price=_number(row.get("f2")),
        change_percent=_number(row.get("f3")),
        change_amount=_number(row.get("f4")),
        volume=_number(row.get("f5")),
        amount=_number(row.get("f6")),
        previous_close=_number(row.get("f18")),
        update_time=update_time,
    )
