"""QA harness for the Assistant module.

Connects to the live backend ws/chat endpoint, sends a question (optionally with a
symbol + prior turns), and returns the fully-streamed assistant reply. Used by the
/loop QA cycle to probe the assistant with realistic user questions and judge the
reasonableness of its answers.

Usage:
    python ask.py "What's the current price of AAPL?" --symbol AAPL
    echo '{"messages":[...],"symbol":"BTC/USDT"}' | python ask.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets

WS_URL = "ws://localhost:8051/api/ws/chat"


async def ask(messages: list[dict], symbol: str | None = None, timeout: float = 120.0) -> dict:
    t0 = time.time()
    out: list[str] = []
    err = None
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({"messages": messages, "symbol": symbol}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                err = "timeout"
                break
            m = json.loads(raw)
            if m.get("type") == "token":
                out.append(m["text"])
            elif m.get("type") == "done":
                break
            elif m.get("type") == "error":
                err = m.get("detail")
                break
            elif m.get("type") == "tool":
                # surfaced tool activity (added by the grounding work)
                out.append(f"\n[tool:{m.get('name')} {json.dumps(m.get('args', {}))}]\n")
    return {"text": "".join(out), "error": err, "secs": round(time.time() - t0, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--json", action="store_true", help="read {messages,symbol} from stdin")
    args = ap.parse_args()

    if args.json:
        payload = json.load(sys.stdin)
        messages = payload["messages"]
        symbol = payload.get("symbol")
    else:
        messages = [{"role": "user", "content": args.query}]
        symbol = args.symbol

    res = asyncio.run(ask(messages, symbol))
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
