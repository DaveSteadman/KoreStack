# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI route group for version and model status endpoints.
#
# Registered into the FastAPI app by register_status_routes(), called from server.py.
#
# Endpoints:
#   GET /version       -- return the suite version string
#   GET /status/ollama -- active host, model, num_ctx, backend, and 'ollama ps' row list
#
# Related modules:
#   - input_layer/server.py  -- registers this route group
#   - llm_client.py          -- get_ollama_ps_rows, get_active_host/model/num_ctx/backend
# ====================================================================================================
from datetime import datetime


def register_status_routes(
    app,
    *,
    get_active_host,
    get_active_model,
    get_active_num_ctx,
    get_active_backend,
    get_ollama_ps_rows,
    get_startup_state,
    get_version_text,
) -> None:
    @app.get("/api/version")
    @app.get("/version", include_in_schema=False)
    def get_version():
        return {"version": get_version_text()}

    @app.get("/api/status")
    @app.get("/status", include_in_schema=False)
    def get_service_status():
        startup      = get_startup_state() or {}
        dependencies = startup.get("dependencies") or {}
        statuses     = [entry.get("status") for entry in dependencies.values() if isinstance(entry, dict)]
        if any(status == "degraded" for status in statuses):
            service_status = "degraded"
            message        = "Running with degraded dependencies"
        elif any(status == "pending" for status in statuses):
            service_status = "starting"
            message        = "Dependency warmup in progress"
        else:
            service_status = "ready"
            message        = "Ready"
        return {
            "service":        "KoreAgent",
            "status":         service_status,
            "message":        message,
            "started_at":     startup.get("started_at"),
            "dependencies":   dependencies,
            "host":           get_active_host(),
            "model":          get_active_model(),
            "num_ctx":        get_active_num_ctx(),
            "backend":        get_active_backend(),
            "ts":             datetime.now().isoformat(timespec="seconds"),
        }

    @app.get("/api/status/ollama")
    @app.get("/status/ollama", include_in_schema=False)
    def get_ollama_status():
        try:
            rows = get_ollama_ps_rows()
        except Exception:
            rows = []
        return {
            "host":    get_active_host(),
            "model":   get_active_model(),
            "num_ctx": get_active_num_ctx(),
            "backend": get_active_backend(),
            "rows":    rows,
            "ts":      datetime.now().isoformat(timespec="seconds"),
        }
