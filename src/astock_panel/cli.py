from __future__ import annotations

import argparse
import os
import time

from .config import load_config
from .market_data import QuoteError, fetch_quotes


def main() -> None:
    parser = argparse.ArgumentParser(description="A 股行情终端模式")
    parser.add_argument("--watch", action="store_true", help="持续刷新")
    parser.add_argument("--interval", type=int, default=None, help="刷新间隔秒数")
    args = parser.parse_args()

    config = load_config()
    interval = max(10, args.interval or config.refresh_seconds)

    while True:
        _clear()
        try:
            quotes = fetch_quotes(config.symbols)
        except QuoteError as exc:
            print(f"行情获取失败: {exc}")
        else:
            print("A Stock Panel - 终端模式")
            print("-" * 54)
            for quote in quotes:
                print(
                    f"{quote.update_time:>8}  {quote.name:<8}  "
                    f"{_price(quote.price):>10}  {_percent(quote.change_percent):>8}"
                )
            print("-" * 54)
            print(f"刷新间隔: {interval} 秒    Ctrl+C 退出")

        if not args.watch:
            break
        time.sleep(interval)


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _price(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _percent(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


if __name__ == "__main__":
    main()
