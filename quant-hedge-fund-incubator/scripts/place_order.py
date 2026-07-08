"""Place a single PAPER order on Alpaca through the framework's AlpacaPaperBroker.

Loads ALPACA_* from the project .env (so secrets stay out of the shell history / chat), then
submits one market order and prints the order id + status + an account snapshot.

  # 1. put your paper keys in .env:  ALPACA_API_KEY=...  ALPACA_API_SECRET=...  ALPACA_PAPER=true
  # 2. run (defaults: buy 1 AAPL, market, DAY):
  .venv\\Scripts\\python.exe scripts\\place_order.py            # AAPL 1 buy
  .venv\\Scripts\\python.exe scripts\\place_order.py MSFT 2 sell

Refuses to run against a LIVE account unless --live is passed explicitly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.execution.base import Order, OrderSide
from qhfi.execution.brokers.alpaca_paper import AlpacaPaperBroker


def _load_dotenv() -> None:
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith("ALPACA_") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    _load_dotenv()
    args = [a for a in sys.argv[1:] if a != "--live"]
    live = "--live" in sys.argv
    symbol = args[0] if len(args) > 0 else "AAPL"
    qty = float(args[1]) if len(args) > 1 else 1.0
    side = OrderSide.SELL if (len(args) > 2 and args[2].lower() == "sell") else OrderSide.BUY

    key, sec = os.environ.get("ALPACA_API_KEY", ""), os.environ.get("ALPACA_API_SECRET", "")
    if not (key and sec):
        print("No ALPACA_API_KEY / ALPACA_API_SECRET found (env or .env).")
        print("Add your paper keys to .env, then re-run. Get them at alpaca.markets → Paper Trading.")
        sys.exit(1)

    paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"
    if not paper and not live:
        print("ALPACA_PAPER=false (LIVE account). Refusing without explicit --live. Aborting.")
        sys.exit(1)

    broker = AlpacaPaperBroker.from_env()
    acct = broker.get_account()
    print(f"Account: equity={acct.equity} cash={acct.cash} (paper={paper})")
    print(f"Submitting: {side.value.upper()} {qty} {symbol} (market, DAY)…")
    order_id = broker.submit(Order(instrument_id=symbol, side=side, quantity=qty))
    print(f"  → order id: {order_id}")
    print("  (check fills/status on the Alpaca paper dashboard)")


if __name__ == "__main__":
    main()
