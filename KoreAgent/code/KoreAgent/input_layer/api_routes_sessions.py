import queue
import threading
import uuid

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


class PromptRequest(BaseModel):
    prompt: str


def register_session_routes(
    app,
    *,
    config_getter,
    validate_session_id,
    make_run_event_queue,
    queue_run_event,
    finish_run_event_queue,
    handle_stoprun_immediate,
    load_session,
    save_session,
    flush_scratch_session,
    build_summary_block,
    create_session_context,
    clear_session_scratch,
    make_slash_context,
    handle_slash,
    push_log_line,
    log_file_re,
    turn_agent_re,
    turn_metrics_re,
    test_complete_re,
    set_latest_log_path,
    log_dir,
    create_log_file_path,
    session_logger_cls,
    orchestrate_prompt,
    get_active_num_ctx,
    task_queue,
    run_queues,
    run_queues_lock,
    sse,
    delete_session_state,
    kc_save_turn,
    get_session_turns,
    kc_set_session_name=None,
) -> None:
    @app.post("/sessions/{session_id}/prompt")
    def post_prompt(session_id: str, body: PromptRequest):
        validate_session_id(session_id)
        config = config_getter()
        if not body.prompt or not body.prompt.strip():
            raise HTTPException(status_code=400, detail="prompt cannot be empty")
        if config is None:
            raise HTTPException(status_code=503, detail="API not yet initialised")

        prompt_text = body.prompt.strip()
        run_id = f"api_{session_id}_{uuid.uuid4().hex}"
        run_q = make_run_event_queue(run_id)
        if prompt_text.lower() == "/stoprun":
            handle_stoprun_immediate(run_id, run_q)
            return {"run_id": run_id, "session_id": session_id, "queued": True}

        def _run(_prompt=prompt_text) -> None:
            session_context = create_session_context(session_id=session_id, persist_path=None)
            history, summaries = load_session(session_id)
            queue_run_event(run_q, {"type": "start", "run_id": run_id, "prompt": _prompt}, priority=True)
            try:
                if _prompt.startswith("/"):
                    output_lines: list[str] = []
                    streamed_output = False

                    def _slash_output(text: str, level: str = "info") -> None:
                        nonlocal streamed_output
                        output_lines.append(text)
                        push_log_line(f"[slash] {text}")

                        log_match = log_file_re.match(text.strip())
                        if log_match:
                            log_path = log_match.group(1).strip()
                            set_latest_log_path(log_path)
                            queue_run_event(run_q, {"type": "log_file", "run_id": run_id, "path": log_path}, priority=True)
                            streamed_output = True
                            return

                        agent_match = turn_agent_re.match(text)
                        if agent_match:
                            queue_run_event(run_q, {"type": "test_agent_response", "run_id": run_id, "turn": int(agent_match.group(1)), "response": agent_match.group(2)}, priority=True)
                            streamed_output = True
                            return

                        metrics_match = turn_metrics_re.match(text)
                        if metrics_match:
                            queue_run_event(run_q, {"type": "test_agent_metrics", "run_id": run_id, "turn": int(metrics_match.group(1)), "tokens": int(metrics_match.group(2)), "tps": metrics_match.group(3)}, priority=True)
                            streamed_output = True
                            return

                        test_complete_match = test_complete_re.match(text)
                        if test_complete_match:
                            queue_run_event(run_q, {"type": "test_complete", "run_id": run_id, "text": text, "level": level}, priority=True)
                            streamed_output = True
                            return

                        queue_run_event(run_q, {"type": "progress", "run_id": run_id, "text": text, "level": level})
                        streamed_output = True

                    def _do_switch_session(new_session_id: str, name: str) -> None:
                        if kc_set_session_name and name:
                            kc_set_session_name(new_session_id, name)
                        queue_run_event(run_q, {"type": "switch_session", "run_id": run_id, "session_id": new_session_id, "name": name}, priority=True)

                    def _do_rename_session(new_session_id: str, name: str) -> None:
                        queue_run_event(run_q, {"type": "rename_session", "run_id": run_id, "session_id": new_session_id, "name": name}, priority=True)

                    slash_ctx = make_slash_context(
                        config=config,
                        output=_slash_output,
                        clear_history=lambda: (history.clear(), session_context.clear(), clear_session_scratch(session_id), save_session(session_id, history, [], 0, 0)),
                        session_context=session_context,
                        session_id=session_id,
                        switch_session=_do_switch_session,
                        rename_session=_do_rename_session,
                        delete_session_state=delete_session_state,
                    )
                    handled = handle_slash(_prompt, slash_ctx)
                    slash_response = "\n".join(output_lines) if output_lines else ("(done)" if handled else f"Unknown command: {_prompt.split()[0]}")
                    if not streamed_output:
                        queue_run_event(run_q, {"type": "response", "run_id": run_id, "response": slash_response, "tokens": 0, "tps": "0"}, priority=True)
                    # Record the slash command and its output in the KC conversation thread (background).
                    threading.Thread(target=kc_save_turn, args=(session_id, _prompt, slash_response), daemon=True).start()
                else:
                    log_path = create_log_file_path(log_dir=log_dir)
                    set_latest_log_path(log_path)
                    queue_run_event(run_q, {"type": "log_file", "run_id": run_id, "path": str(log_path)}, priority=True)
                    with session_logger_cls(log_path) as run_logger:
                        run_logger.log_section_file_only(f"API SESSION: {session_id}")
                        summary_block = build_summary_block(summaries)
                        response, p_tokens, _completion_tokens, _ok, tps = orchestrate_prompt(
                            user_prompt=_prompt,
                            config=config,
                            logger=run_logger,
                            conversation_history=history.as_list() or None,
                            session_context=session_context,
                            quiet=True,
                            conversation_summary=summary_block or None,
                            on_tool_round_complete=lambda: threading.Thread(
                                target=flush_scratch_session, args=(session_id,), daemon=True
                            ).start(),
                        )
                        history.add(_prompt, response)
                        summaries = save_session(session_id, history, summaries, p_tokens, get_active_num_ctx())
                        queue_run_event(run_q, {"type": "response", "run_id": run_id, "response": response, "tokens": p_tokens, "tps": f'{tps:.1f}' if tps > 0 else '0'}, priority=True)
                        # Persist the turn to KC asynchronously - response is already queued.
                        threading.Thread(target=kc_save_turn, args=(session_id, _prompt, response), daemon=True).start()
            except Exception as exc:
                queue_run_event(run_q, {"type": "error", "run_id": run_id, "message": str(exc)}, priority=True)
            finally:
                finish_run_event_queue(run_id)

        task_queue.enqueue(run_id, "api_chat", _run, label=prompt_text[:48])
        return {"run_id": run_id, "session_id": session_id, "queued": True}

    @app.get("/sessions/{session_id}/history")
    def get_session_history(session_id: str):
        validate_session_id(session_id)
        turns = get_session_turns(session_id)
        return {"session_id": session_id, "turns": turns}

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str):
        """Delete all persisted state for a session."""
        validate_session_id(session_id)
        delete_session_state(session_id)
        return {"ok": True, "session_id": session_id}

    @app.get("/runs/{run_id}/stream")
    def stream_run(run_id: str):
        with run_queues_lock:
            run_q = run_queues.get(run_id)
        if run_q is None:
            raise HTTPException(status_code=404, detail="Run ID not found or already completed")

        def _generate():
            while True:
                try:
                    item = run_q.get(timeout=2.0)
                    if item is None:
                        with run_queues_lock:
                            run_queues.pop(run_id, None)
                        yield sse({"type": "done", "run_id": run_id})
                        break
                    yield sse(item)
                except queue.Empty:
                    yield ": keepalive\n\n"

        return StreamingResponse(_generate(), media_type="text/event-stream")
