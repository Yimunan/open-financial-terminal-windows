"""Multiplexed realtime websocket: one socket per browser tab, many topics.

Protocol (client → server): ``{"op": "sub"|"unsub", "topic": "book:binance:BTC/USDT"}``.
Server → client: ``{"topic", "type": "ticker"|"book"|"trades"|"status"|"error", "data"}``.
Fan-out and 150ms coalescing live in :mod:`app.services.realtime`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.realtime import get_hub

router = APIRouter(tags=["stream"])

QUEUE_MAX = 500  # frames buffered per connection before drops (slow consumer guard)


@router.get("/api/stream/stats")
def stream_stats() -> dict:
    return get_hub().stats()


@router.websocket("/api/ws/stream")
async def stream(ws: WebSocket) -> None:
    await ws.accept()
    hub = get_hub()
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)

    async def _sender() -> None:
        while True:
            await ws.send_json(await queue.get())

    sender = asyncio.create_task(_sender())
    try:
        while True:
            msg = await ws.receive_json()
            op, topic = msg.get("op"), msg.get("topic", "")
            try:
                if op == "sub":
                    await hub.subscribe(topic, queue)
                elif op == "unsub":
                    await hub.unsubscribe(topic, queue)
                else:
                    _offer_error(queue, topic, f"unknown op '{op}'")
            except ValueError as e:
                _offer_error(queue, topic, str(e))
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        await hub.drop_queue(queue)


def _offer_error(queue: asyncio.Queue, topic: str, message: str) -> None:
    try:
        queue.put_nowait({"topic": topic, "type": "error", "data": {"message": message}})
    except asyncio.QueueFull:
        pass
