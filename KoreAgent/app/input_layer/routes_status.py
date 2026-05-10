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


def register_status_routes(app, *, get_active_host, get_active_model, get_active_num_ctx, get_active_backend, get_ollama_ps_rows, version: str) -> None:
    @app.get("/version")
    def get_version():
        return {"version": version}

    @app.get("/status/ollama")
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
