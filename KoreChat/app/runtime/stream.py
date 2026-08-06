import asyncio
import json
import threading

from fastapi.responses import StreamingResponse


_subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
_subscribers_lock: threading.Lock = threading.Lock()


def push_event(event_type: str, conversation_id: int | None = None) -> None:
    item = {"type": event_type}
    if conversation_id is not None:
        item["conversation_id"] = conversation_id
    with _subscribers_lock:
        subscribers = list(_subscribers)
    for loop, subscriber in subscribers:
        if loop.is_closed():
            with _subscribers_lock:
                if (loop, subscriber) in _subscribers:
                    _subscribers.remove((loop, subscriber))
            continue
        loop.call_soon_threadsafe(_enqueue_event, subscriber, item)


def _enqueue_event(subscriber: asyncio.Queue, item: dict) -> None:
    if not subscriber.full():
        subscriber.put_nowait(item)


async def event_stream_response() -> StreamingResponse:
    subscriber: asyncio.Queue = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()
    with _subscribers_lock:
        _subscribers.append((loop, subscriber))

    async def generate():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(subscriber.get(), timeout=20)
                    yield f"data: {json.dumps(item)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            with _subscribers_lock:
                try:
                    _subscribers.remove((loop, subscriber))
                except ValueError:
                    pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
