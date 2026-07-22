from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Session persistence and KoreChat synchronisation service extracted from the
# input-layer server.
#
# Responsibilities:
#   - map web session ids to KoreChat conversations
#   - cache KoreChat conversation lookups behind a dedicated lock
#   - persist turns, scratchpad state, datasets, and compaction outputs
#   - coordinate /stoprun behaviour with the shared task queue and per-run event
#     queues exposed by the input layer
#
# Concurrency note:
#   - _kc_conv_cache is only an optimisation.  Correctness comes from KoreChat as
#     the source of truth, so cache misses or cache resets must remain recoverable.
# ====================================================================================================

import json
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request

import httpx
from fastapi import HTTPException
from conversation_state import build_background_turn
from conversation_state import decode_background_context
from conversation_state import extract_named_items
from conversation_state import encode_background_context
from sessions.tool_selection import clear_session_tools_active


class SessionService:
    def __init__(
        self,
        *,
        compact_fill_pct: float,
        kc_client,
        conversation_history_cls,
        session_context_cls,
        hydrate_session_state,
        scratchpad_clear,
        scratchpad_restore_key,
        get_scratchpad_store,
        build_persisted_scratchpad_payload,
        get_persisted_datasets_payload,
        delete_persisted_session_datasets,
        request_stop,
        task_queue,
        run_event_queues: dict[str, queue.Queue],
        run_queues_lock: threading.Lock,
        queue_run_event,
        finish_run_event_queue,
    ) -> None:
        self._compact_fill_pct                    = compact_fill_pct
        self._kc_client                           = kc_client
        self._conversation_history_cls            = conversation_history_cls
        self._session_context_cls                 = session_context_cls
        self._hydrate_session_state               = hydrate_session_state
        self._scratchpad_clear                       = scratchpad_clear
        self._scratchpad_restore_key                 = scratchpad_restore_key
        self._get_scratchpad_store                   = get_scratchpad_store
        self._build_persisted_scratchpad_payload  = build_persisted_scratchpad_payload
        self._get_persisted_datasets_payload      = get_persisted_datasets_payload
        self._delete_persisted_session_datasets   = delete_persisted_session_datasets
        self._request_stop                        = request_stop
        self._task_queue                          = task_queue
        self._run_event_queues                    = run_event_queues
        self._run_queues_lock                     = run_queues_lock
        self._queue_run_event                     = queue_run_event
        self._finish_run_event_queue              = finish_run_event_queue
        self._kc_conv_cache: dict[str, dict]     = {}
        self._kc_conv_cache_lock                  = threading.Lock()
        self._kc_session_names: dict[str, str]   = {}
        self._kc_direct_session_prefix            = "kc_conv_"
        self._kc_timeout                          = 8
        self._max_recent_turns                    = 4

    def create_session_context(self, *, session_id: str, persist_path=None):
        session_context = self._conversation_history_session_context(session_id=session_id, persist_path=persist_path)
        conv            = self.kc_get_conversation_for_session(session_id)
        if conv is None:
            return session_context

        background_ctx = (conv.get("background_context") or "").strip()
        if not background_ctx:
            legacy_summary = (conv.get("thread_summary") or "").strip()
            if not legacy_summary:
                return session_context
            with session_context._lock:
                session_context._turns = [
                    build_background_turn(
                        turn               = 1,
                        user_prompt        = "[legacy summary]",
                        assistant_response = legacy_summary,
                        skill_outputs      = [],
                    )
                ]
            return session_context

        restored_turns, _warning = decode_background_context(background_ctx)
        if restored_turns:
            with session_context._lock:
                session_context._turns = restored_turns
        return session_context

    def _conversation_history_session_context(self, *, session_id: str, persist_path=None):
        return self._session_context_cls(session_id=session_id, persist_path=persist_path)

    def handle_stoprun_immediate(self, run_id: str, run_q: queue.Queue) -> None:
        self._request_stop("stoprun")
        cancelled_ids = self._task_queue.clear_pending()
        cancel_msg    = "Cancelled by /stoprun."
        for rid in cancelled_ids:
            with self._run_queues_lock:
                q = self._run_event_queues.get(rid)
            if q is None:
                continue
            self._queue_run_event(q, {"type": "response", "run_id": rid, "response": cancel_msg, "tokens": 0, "tps": "0"}, priority=True)
            self._queue_run_event(q, None, priority=True)

        count       = len(cancelled_ids)
        active_note = "Active run will halt after its current LLM round. "
        summary     = (
            f"{active_note}{count} pending prompt{'s' if count != 1 else ''} cancelled."
            if count else
            f"{active_note}No prompts were queued."
        )
        self._queue_run_event(run_q, {"type": "response", "run_id": run_id, "response": summary, "tokens": 0, "tps": "0"}, priority=True)
        self._finish_run_event_queue(run_id)

    def kc_external_id_for_session(self, session_id: str) -> str:
        return f"webchat_{session_id}"

    def kc_conversation_id_for_session(self, session_id: str) -> int | None:
        if not session_id.startswith(self._kc_direct_session_prefix):
            return None
        raw = session_id[len(self._kc_direct_session_prefix):].strip()
        return int(raw) if raw.isdigit() else None

    def kc_set_session_name(self, session_id: str, name: str) -> None:
        if name:
            self._kc_session_names[session_id] = name
        else:
            self._kc_session_names.pop(session_id, None)

    def kc_get_conversation_for_session(self, session_id: str) -> dict | None:
        with self._kc_conv_cache_lock:
            if session_id in self._kc_conv_cache:
                return self._kc_conv_cache[session_id]
        try:
            conv_id = self.kc_conversation_id_for_session(session_id)
            if conv_id is not None:
                result = self.kc_get(f"/conversations/{conv_id}")
            else:
                external_id = self.kc_external_id_for_session(session_id)
                result      = self.kc_get(f"/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}")
        except HTTPException as exc:
            if exc.status_code in {404, 503}:
                return None
            raise
        conv = result if isinstance(result, dict) else None
        if conv is not None:
            # Cache writes stay behind the same lock so threads do not race between
            # duplicate fetches and partially updated cache state.
            with self._kc_conv_cache_lock:
                self._kc_conv_cache[session_id] = conv
        return conv

    def get_session_turns(self, session_id: str) -> list[dict]:
        try:
            conv_id = self.kc_conversation_id_for_session(session_id)
            if conv_id is not None:
                messages = self.kc_get(f"/conversations/{conv_id}/messages?limit=1000")
                result   = {"messages": messages if isinstance(messages, list) else []}
            else:
                external_id = self.kc_external_id_for_session(session_id)
                result      = self.kc_get(f"/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}/turns")
        except HTTPException as exc:
            if exc.status_code in {404, 503}:
                return []
            raise
        if not isinstance(result, dict):
            return []
        messages = result.get("messages") or []
        turns: list[dict] = []
        pending_prompt: str | None = None
        for message in messages:
            direction = message.get("direction")
            content   = (message.get("content") or "").strip()
            if not content:
                continue
            if direction == "inbound":
                pending_prompt = content
            elif direction == "outbound" and pending_prompt is not None:
                turns.append({"role": "user", "content": pending_prompt})
                turns.append({"role": "assistant", "content": content})
                pending_prompt = None
        return turns

    def kc_ensure_conversation(self, session_id: str) -> dict | None:
        conv = self.kc_get_conversation_for_session(session_id)
        if conv is not None:
            return conv
        if self.kc_conversation_id_for_session(session_id) is not None:
            return None
        try:
            external_id  = self.kc_external_id_for_session(session_id)
            pending_name = self._kc_session_names.pop(session_id, None)
            subject      = pending_name or f"Webchat {session_id}"
            conv         = self.kc_post("/conversations", {
                "channel_type": "webchat",
                "subject":      subject,
                "protected":    bool(pending_name),
                "external_id":  external_id,
            })
        except Exception:
            # KoreChat persistence is best-effort for web sessions; the caller can
            # continue serving the current run even if background mirroring fails.
            return None
        if isinstance(conv, dict):
            with self._kc_conv_cache_lock:
                self._kc_conv_cache[session_id] = conv
            return conv
        return None

    def kc_save_turn(self, session_id: str, user_text: str, agent_text: str, token_estimate: int | None = None) -> None:
        conv = self.kc_ensure_conversation(session_id)
        if conv is None:
            return
        conv_id = conv["id"]
        try:
            self.kc_post(f"/conversations/{conv_id}/turns", {
                "inbound_content":  user_text,
                "outbound_content": agent_text,
                "inbound_sender":   session_id,
                "outbound_sender":  "agent",
                "token_estimate":   token_estimate,
            })
        except Exception:
            pass
        with self._kc_conv_cache_lock:
            self._kc_conv_cache.pop(session_id, None)

    def load_session(self, session_id: str):
        history = self._conversation_history_cls()
        conv    = self.kc_get_conversation_for_session(session_id)
        if conv is None:
            return history

        self._hydrate_session_state(
            conv.get("scratchpad") or {},
            session_id,
            datasets_payload = conv.get("datasets") or {},
            scratchpad_clearer  = self._scratchpad_clear,
            scratchpad_restorer = self._scratchpad_restore_key,
            warning_logger   = lambda message: print(f"[session] Warning: {message}", flush=True),
        )

        try:
            messages = self.kc_get(f"/conversations/{conv['id']}/messages?summarised=0&limit=1000") or []
        except HTTPException:
            messages = []

        pending_prompt: str | None = None
        for message in messages:
            direction = message.get("direction")
            content   = (message.get("content") or "").strip()
            if not content:
                continue
            if direction == "inbound":
                pending_prompt = content
                continue
            if direction == "outbound" and pending_prompt is not None:
                history.add(pending_prompt, content)
                pending_prompt = None
        return history

    def _history_turns(self, history) -> list[dict]:
        raw   = history.as_list()
        turns = []
        for index in range(0, len(raw) - 1, 2):
            if raw[index]["role"] == "user" and raw[index + 1]["role"] == "assistant":
                turns.append(
                    {
                        "user_prompt":        raw[index]["content"],
                        "assistant_response": raw[index + 1]["content"],
                    }
                )
        return turns

    def _promote_named_items(self, session_id: str, history) -> None:
        turns = self._history_turns(history)
        if not turns:
            return
        latest = turns[-1]
        named  = extract_named_items(latest["user_prompt"], latest["assistant_response"])
        if not named:
            return
        for key, value in named.items():
            self._scratchpad_restore_key(key, value, session_id=session_id)

    def _archive_old_history(self, history, session_context, *, prompt_tokens: int, num_ctx: int) -> None:
        if num_ctx <= 0 or prompt_tokens <= 0:
            return
        if (prompt_tokens / num_ctx) < self._compact_fill_pct:
            return

        turn_dicts = self._history_turns(history)
        if len(turn_dicts) <= self._max_recent_turns:
            return

        archived = turn_dicts[: len(turn_dicts) - self._max_recent_turns]
        recent   = turn_dicts[len(turn_dicts) - self._max_recent_turns :]
        with session_context._lock:
            next_turn = len(session_context._turns)
            for turn in archived:
                next_turn += 1
                session_context._turns.append(
                    build_background_turn(
                        turn               = next_turn,
                        user_prompt        = turn["user_prompt"],
                        assistant_response = turn["assistant_response"],
                        skill_outputs      = [],
                    )
                )

        history.clear()
        for turn in recent:
            history.add(turn["user_prompt"], turn["assistant_response"])

    def save_session(self, session_id: str, history, session_context, prompt_tokens: int, num_ctx: int) -> None:
        self._promote_named_items(session_id, history)
        self._archive_old_history(history, session_context, prompt_tokens=prompt_tokens, num_ctx=num_ctx)
        self.flush_scratch_to_session(session_id)

        conv = self.kc_get_conversation_for_session(session_id)
        if conv is None:
            return
        try:
            named_scratch = {
                key: value
                for key, value in self._get_scratchpad_store(session_id).items()
                if not key.startswith(("_tc_", "_cx_", "research_page_"))
            }
            archived_turns = session_context.get_turns()
            persisted_background = (
                encode_background_context(archived_turns, conv.get("background_context") or "")
                if archived_turns or self._history_turns(history)
                else ""
            )
            self.kc_patch(
                f"/conversations/{conv['id']}",
                {
                    "scratchpad":         self._build_persisted_scratchpad_payload(named_scratch),
                    "datasets":           self._get_persisted_datasets_payload(session_id),
                    "background_context": persisted_background,
                },
            )
        except Exception as exc:
            print(f"[session] Warning: could not persist background_context for session '{session_id}': {exc}", flush=True)

    def flush_scratch_to_session(self, session_id: str) -> None:
        conv = self.kc_get_conversation_for_session(session_id)
        if conv is None:
            return
        try:
            named_scratch = {
                key: value
                for key, value in self._get_scratchpad_store(session_id).items()
                if not key.startswith(("_tc_", "_cx_", "research_page_"))
            }
            self.kc_patch(
                f"/conversations/{conv['id']}",
                {
                    "scratchpad": self._build_persisted_scratchpad_payload(named_scratch),
                    "datasets":   self._get_persisted_datasets_payload(session_id),
                },
            )
        except Exception as exc:
            print(f"[session] Warning: could not flush scratchpad to KoreChat for session '{session_id}': {exc}", flush=True)
        finally:
            with self._kc_conv_cache_lock:
                self._kc_conv_cache.pop(session_id, None)

    def delete_session_state(self, session_id: str) -> None:
        self._scratchpad_clear(session_id)
        self._delete_persisted_session_datasets(session_id)
        clear_session_tools_active(session_id)
        with self._kc_conv_cache_lock:
            self._kc_conv_cache.pop(session_id, None)
        conv = self.kc_get_conversation_for_session(session_id)
        if conv is None:
            return
        try:
            self.kc_delete(f"/conversations/{conv['id']}")
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

    def kc_get(self, path: str) -> dict | list | None:
        base = self._kc_client.get_base_url()
        if not base:
            raise HTTPException(status_code=503, detail="KoreChat not configured")
        req = urllib.request.Request(f"{base}{path}", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._kc_timeout) as resp:
                if resp.status == 204:
                    return None
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise HTTPException(status_code=exc.code, detail=exc.read().decode("utf-8", errors="replace")[:200]) from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc.reason}") from exc

    def kc_post(self, path: str, payload: dict) -> dict | None:
        return self._kc_write("POST", path, payload)

    def kc_patch(self, path: str, payload: dict) -> dict | None:
        return self._kc_write("PATCH", path, payload)

    def kc_delete(self, path: str) -> None:
        base = self._kc_client.get_base_url()
        if not base:
            raise HTTPException(status_code=503, detail="KoreChat not configured")
        req = urllib.request.Request(f"{base}{path}", method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=self._kc_timeout):
                return None
        except urllib.error.HTTPError as exc:
            raise HTTPException(status_code=exc.code, detail=exc.read().decode("utf-8", errors="replace")[:200]) from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc.reason}") from exc

    def _kc_write(self, method: str, path: str, payload: dict) -> dict | None:
        base = self._kc_client.get_base_url()
        if not base:
            raise HTTPException(status_code=503, detail="KoreChat not configured")
        req = urllib.request.Request(
            f"{base}{path}",
            data    = json.dumps(payload).encode("utf-8"),
            method  = method,
            headers = {"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._kc_timeout) as resp:
                raw = resp.read().decode("utf-8").strip()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise HTTPException(status_code=exc.code, detail=exc.read().decode("utf-8", errors="replace")[:200]) from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc.reason}") from exc

    async def kc_request_async(self, method: str, path: str, payload: dict | None = None) -> dict | list | None:
        base = self._kc_client.get_base_url()
        if not base:
            raise HTTPException(status_code=503, detail="KoreChat not configured")
        request_kwargs: dict = {"headers": {"Accept": "application/json"}}
        if payload is not None:
            request_kwargs["json"] = payload
        try:
            async with httpx.AsyncClient(timeout=self._kc_timeout) as client:
                response = await client.request(method, f"{base}{path}", **request_kwargs)
        except httpx.ConnectError as exc:
            raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="KoreChat request timed out") from exc

        if response.status_code == 204:
            return None
        if response.is_error:
            detail = response.text[:200] if response.text else f"KoreChat HTTP {response.status_code}"
            raise HTTPException(status_code=response.status_code, detail=detail)
        body = response.text.strip()
        return json.loads(body) if body else None

    async def kc_get_async(self, path: str) -> dict | list | None:
        return await self.kc_request_async("GET", path)

    async def kc_post_async(self, path: str, payload: dict) -> dict | None:
        response = await self.kc_request_async("POST", path, payload)
        return response if isinstance(response, dict) else None
