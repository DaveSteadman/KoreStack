import queue
import time
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import StreamingResponse


def register_log_routes(
    app,
    *,
    log_dir: Path,
    shutdown_event_getter,
    log_poll_secs: float,
    sse,
    set_latest_log_path,
    get_latest_log_file,
    get_log_backfill,
    log_subscribers,
    log_subscribers_lock,
) -> None:
    @app.get("/logs")
    def list_logs():
        result = []
        if log_dir.exists():
            for day_dir in sorted(log_dir.iterdir(), reverse=True):
                if day_dir.is_dir():
                    files = sorted([file_path.name for file_path in day_dir.glob("*.txt")], reverse=True)
                    result.append({"date": day_dir.name, "files": files})
        return {"log_dirs": result}

    @app.get("/logs/latest")
    def get_latest_log():
        latest = get_latest_log_file()
        if latest is None:
            return {"path": None}
        set_latest_log_path(latest)
        return {"path": str(latest)}

    @app.get("/logs/{date}/{filename}")
    def get_log_file(date: str, filename: str):
        if not all(char.isalnum() or char in "-_." for char in date + filename):
            raise HTTPException(status_code=400, detail="Invalid path characters")
        log_path = log_dir / date / filename
        if not log_path.exists() or not log_path.is_file():
            raise HTTPException(status_code=404, detail="Log file not found")
        try:
            log_path.resolve().relative_to(log_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Path outside log directory")
        return {"lines": log_path.read_text(encoding="utf-8", errors="replace").splitlines()}

    @app.get("/logs/stream")
    def stream_logs():
        backfill = get_log_backfill()

        def _generate():
            for item in backfill:
                yield sse(item)

            subscriber: queue.Queue = queue.Queue(maxsize=1000)
            with log_subscribers_lock:
                log_subscribers.append(subscriber)
            try:
                while True:
                    shutdown_event = shutdown_event_getter()
                    if shutdown_event.is_set():
                        break
                    try:
                        item = subscriber.get(timeout=log_poll_secs)
                        yield sse(item)
                    except queue.Empty:
                        yield ": keepalive\n\n"
            except GeneratorExit:
                pass
            finally:
                with log_subscribers_lock:
                    try:
                        log_subscribers.remove(subscriber)
                    except ValueError:
                        pass

        return StreamingResponse(_generate(), media_type="text/event-stream")

    @app.get("/logs/file")
    def stream_log_file(path: str):
        try:
            requested = Path(path).resolve()
            requested.relative_to(log_dir.resolve())
        except (ValueError, OSError):
            raise HTTPException(status_code=400, detail="Path outside log directory")
        if not requested.is_file():
            raise HTTPException(status_code=404, detail="Log file not found")

        def _generate():
            try:
                content = requested.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    yield sse({"type": "log", "text": line, "ts": ""})
            except Exception:
                pass

            try:
                offset = requested.stat().st_size
            except OSError:
                offset = 0

            try:
                while True:
                    shutdown_event = shutdown_event_getter()
                    if shutdown_event.is_set():
                        break
                    try:
                        size = requested.stat().st_size
                        if size > offset:
                            with requested.open("rb") as handle:
                                handle.seek(offset)
                                new_bytes = handle.read()
                            offset = size
                            for line in new_bytes.decode("utf-8", errors="replace").splitlines():
                                if line:
                                    yield sse({"type": "log", "text": line, "ts": datetime.now().isoformat(timespec="seconds")})
                        else:
                            yield ": keepalive\n\n"
                    except OSError:
                        yield ": keepalive\n\n"
                    time.sleep(log_poll_secs)
            except GeneratorExit:
                pass

        return StreamingResponse(_generate(), media_type="text/event-stream")
