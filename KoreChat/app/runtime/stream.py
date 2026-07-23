import json
import queue
import threading

from fastapi.responses import StreamingResponse


_subscribers: list[queue.Queue] = []
_subscribers_lock: threading.Lock = threading.Lock()


def push_event(event_type: str, conversation_id: int | None = None) -> None:
    item = {"type": event_type}
    if conversation_id is not None:
        item["conversation_id"] = conversation_id
    with _subscribers_lock:
        dead: list[queue.Queue] = []
        for subscriber in _subscribers:
            try:
                subscriber.put_nowait(item)
            except queue.Full:
                dead.append(subscriber)
        for subscriber in dead:
            try:
                _subscribers.remove(subscriber)
            except ValueError:
                pass


def event_stream_response() -> StreamingResponse:
    subscriber: queue.Queue = queue.Queue(maxsize=64)
    with _subscribers_lock:
        _subscribers.append(subscriber)

    def generate():
        try:
            while True:
                try:
                    item = subscriber.get(timeout=20)
                    yield f"data: {json.dumps(item)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _subscribers_lock:
                try:
                    _subscribers.remove(subscriber)
                except ValueError:
                    pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
