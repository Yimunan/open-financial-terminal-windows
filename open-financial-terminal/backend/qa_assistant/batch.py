"""Run a batch of QA questions against the live assistant and print compact results."""
from __future__ import annotations

import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from ask import ask  # noqa: E402


async def run(cases: list[dict]) -> None:
    for i, c in enumerate(cases, 1):
        msgs = c.get("messages") or [{"role": "user", "content": c["q"]}]
        res = await ask(msgs, c.get("symbol"))
        txt = res["text"].replace("\n", " ").strip()
        print(f"\n=== [{i}] sym={c.get('symbol')} q={c.get('q', '(multi)')!r} ({res['secs']}s err={res['error']})")
        print(txt[:600])


if __name__ == "__main__":
    cases = json.load(open(sys.argv[1], encoding="utf-8"))
    asyncio.run(run(cases))
