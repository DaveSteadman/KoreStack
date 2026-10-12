from __future__ import annotations

import bdb
import linecache
import threading
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .workspace_service import WorkspaceService


class DebugSession(bdb.Bdb):
    """A single cooperative Python debug session, paused at source lines only."""

    def __init__(self, workspace: WorkspaceService, path: str, breakpoints: list[int]) -> None:
        super().__init__()
        self._workspace = workspace
        self._path = path
        self._source = workspace.resolve_relative_path(path).resolve()
        self._root = workspace.workspace_root().resolve()
        self._runtime_source = Path(__file__).resolve()
        self._condition = threading.Condition()
        self._command: str | None = None
        self._pause_requested = False
        self._frame = None
        self._snapshot: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "path": path,
            "status": "starting",
            "breakpoints": sorted(set(breakpoints)),
            "frames": [],
            "locals": [],
            "error": None,
            "pause_reason": None,
        }
        for line in self._snapshot["breakpoints"]:
            self.set_break(str(self._source), line)

    @property
    def id(self) -> str:
        return self._snapshot["id"]

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"korecode-debug-{self.id}", daemon=True).start()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                **self._snapshot,
                "frames": list(self._snapshot["frames"]),
                "locals": list(self._snapshot["locals"]),
            }

    def control(self, action: str) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        if normalized not in {"continue", "step", "next", "pause", "stop"}:
            raise HTTPException(status_code=400, detail=f"Unsupported debug action: {action}")

        with self._condition:
            status = self._snapshot["status"]
            if normalized == "pause":
                if status == "running":
                    self._pause_requested = True
                return self.snapshot()
            if normalized == "stop" and status == "running":
                self.set_quit()
                self._snapshot["status"] = "stopping"
                return self.snapshot()
            if status != "paused":
                raise HTTPException(status_code=409, detail="Debugger is not paused")
            self._command = normalized
            self._snapshot["status"] = "running" if normalized != "stop" else "stopping"
            self._condition.notify_all()
            return self.snapshot()

    def user_line(self, frame) -> None:
        if not self._is_workspace_frame(frame):
            return
        pause_reason = "step"
        if self._pause_requested:
            self._pause_requested = False
            pause_reason = "pause"
        elif self._is_breakpoint_line(frame):
            pause_reason = "breakpoint"
        self._pause(frame, pause_reason)

    def user_exception(self, frame, exc_info) -> None:
        if not self._is_workspace_frame(frame):
            return
        exc_type, exc, _trace = exc_info
        with self._condition:
            self._snapshot["error"] = f"{exc_type.__name__}: {exc}"
        self._pause(frame, "exception")

    def _run(self) -> None:
        globals_dict = {
            "__name__": "__main__",
            "__file__": str(self._source),
            "__package__": None,
        }
        try:
            code = compile(self._source.read_text(encoding="utf-8"), str(self._source), "exec")
            self.set_step()
            self.runctx(code, globals_dict, globals_dict)
            with self._condition:
                if self._snapshot["status"] == "stopping":
                    self._snapshot["status"] = "stopped"
                elif self._snapshot["status"] not in {"stopped", "error"}:
                    self._snapshot["status"] = "completed"
        except bdb.BdbQuit:
            with self._condition:
                self._snapshot["status"] = "stopped"
        except Exception:
            with self._condition:
                self._snapshot["status"] = "error"
                self._snapshot["error"] = traceback.format_exc(limit=8)[-4000:]

    def _pause(self, frame, reason: str) -> None:
        with self._condition:
            self._frame = frame
            self._snapshot["status"] = "paused"
            self._snapshot["pause_reason"] = reason
            self._snapshot["frames"] = self._frames(frame)
            self._snapshot["locals"] = self._locals(frame)
            self._command = None
            while self._command is None:
                self._condition.wait()
            command = self._command
            self._command = None

        if command == "continue":
            self.set_continue()
        elif command == "step":
            self.set_step()
        elif command == "next":
            self.set_next(frame)
        elif command == "stop":
            self.set_quit()
            raise bdb.BdbQuit

    def _is_workspace_frame(self, frame) -> bool:
        try:
            Path(frame.f_code.co_filename).resolve().relative_to(self._root)
            return True
        except ValueError:
            return False

    def _is_breakpoint_line(self, frame) -> bool:
        return (
            Path(frame.f_code.co_filename).resolve() == self._source
            and frame.f_lineno in self._snapshot["breakpoints"]
        )

    def _frames(self, frame) -> list[dict[str, Any]]:
        frames = []
        current = frame
        while current is not None and len(frames) < 16:
            if self._is_workspace_frame(current):
                filename = Path(current.f_code.co_filename).resolve()
                if filename != self._runtime_source:
                    frames.append({
                        "path": self._workspace.to_posix(filename),
                        "line": current.f_lineno,
                        "name": current.f_code.co_name,
                        "source": linecache.getline(str(filename), current.f_lineno).strip(),
                    })
            current = current.f_back
        return frames

    @staticmethod
    def _locals(frame) -> list[dict[str, str]]:
        values = []
        for name, value in sorted(frame.f_locals.items()):
            if name.startswith("__"):
                continue
            try:
                rendered = repr(value)
            except Exception:
                rendered = "<unavailable>"
            values.append({
                "name": name,
                "value": rendered[:240],
                "type": type(value).__name__,
            })
            if len(values) == 80:
                break
        return values


class DebugSessionManager:
    def __init__(self, workspace: WorkspaceService) -> None:
        self._workspace = workspace
        self._sessions: dict[str, DebugSession] = {}
        self._lock = threading.Lock()

    def start(self, path: str, breakpoints: list[int]) -> dict[str, Any]:
        candidate = self._workspace.resolve_relative_path(path)
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Python file not found")
        if candidate.suffix.lower() not in {".py", ".pyi"}:
            raise HTTPException(status_code=400, detail="Debugging requires a Python file")
        session = DebugSession(self._workspace, path, breakpoints)
        with self._lock:
            self._sessions[session.id] = session
        session.start()
        return session.snapshot()

    def get(self, session_id: str) -> dict[str, Any]:
        return self._session(session_id).snapshot()

    def control(self, session_id: str, action: str) -> dict[str, Any]:
        return self._session(session_id).control(action)

    def _session(self, session_id: str) -> DebugSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Debug session not found")
        return session
