"""Queue observability route for interactive KoreAgent work."""


def register_queue_routes(
    app,
    *,
    task_queue,
    queue_preview_limit: int,
    get_pending_switch=None,
) -> None:
    @app.get("/api/queue")
    @app.get("/queue", include_in_schema=False)
    def get_queue():
        queue_state    = task_queue.get_state(pending_limit=queue_preview_limit)
        pending_switch = get_pending_switch() if get_pending_switch else None
        return {
            "queued_prompt_count":      queue_state.get("queued_prompt_count", 0),
            "pending_count":            queue_state.get("pending_count", 0),
            "queue_count":              queue_state.get("queue_count", 0),
            "active":                   queue_state.get("active"),
            "next_prompts":             queue_state.get("next_prompts", []),
            "next_prompts_limit":       queue_state.get("next_prompts_limit", queue_preview_limit),
            "active_age_s":             queue_state.get("active_age_s"),
            "active_timeout_s":         queue_state.get("active_timeout_s"),
            "active_timeout_exceeded":  queue_state.get("active_timeout_exceeded", False),
            "active_cancel_requested":  queue_state.get("active_cancel_requested", False),
            "oldest_pending_age_s":     queue_state.get("oldest_pending_age_s"),
            "queue_lag_s":              queue_state.get("queue_lag_s"),
            "stalled":                  queue_state.get("stalled", False),
            "updated_at":               queue_state.get("updated_at"),
            "pending_switch":           pending_switch,
        }
