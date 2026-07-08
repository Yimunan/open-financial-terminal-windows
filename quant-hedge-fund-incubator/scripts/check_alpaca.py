"""Dry connection check for the Alpaca paper broker:
  1. build a real order request (verifies the order-placement mapping end-to-end),
  2. report whether ALPACA_* credentials are present,
  3. attempt a READ-ONLY get_account() — authenticated if keys are set, otherwise a bogus-key
     reach to confirm Alpaca's endpoint is reachable. NEVER submits an order.

  set ALPACA_API_KEY / ALPACA_API_SECRET, then:
  .venv\\Scripts\\python.exe scripts\\check_alpaca.py
"""

from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from qhfi.execution.base import Order, OrderSide
from qhfi.execution.brokers.alpaca_paper import _build_order_request


def main() -> None:
    # 1. order-placement mapping (uses real alpaca-py request classes)
    req = _build_order_request(Order(instrument_id="AAPL", side=OrderSide.BUY, quantity=10))
    print(f"[1] order request OK: {type(req).__name__} "
          f"symbol={req.symbol} qty={req.qty} side={req.side.value} tif={req.time_in_force.value}")

    # 2. credentials
    key, sec = os.environ.get("ALPACA_API_KEY", ""), os.environ.get("ALPACA_API_SECRET", "")
    print(f"[2] credentials present: {bool(key and sec)}")

    # 3. connectivity / auth (read-only)
    from alpaca.trading.client import TradingClient
    try:
        client = TradingClient(key or "NO_KEY", sec or "NO_SECRET", paper=True)
        acct = client.get_account()
        print(f"[3] AUTHENTICATED ✓  account={acct.account_number} status={acct.status} "
              f"equity={acct.equity} buying_power={acct.buying_power} (paper)")
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace("\n", " ")[:160]
        print(f"[3] reached Alpaca, not authenticated: {type(e).__name__}: {msg}")
        if not (key and sec):
            print("    → expected (no keys). Set ALPACA_API_KEY/ALPACA_API_SECRET from your")
            print("      paper dashboard at alpaca.markets and re-run for a live authenticated check.")


if __name__ == "__main__":
    main()
