# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application — WebUI routes + KoreComms REST API.
#
# Serves the messaging management WebUI via Jinja2 templates and exposes a REST API
# consumed by the KoreAgent poller and external clients.
#
# Endpoints:
#   GET  /status                             health check
#   POST /api/send                           send a message via a named interface
#   GET  /                                   dashboard (activity feed)
#   GET  /compose                            compose form (reply/new message)
#   POST /compose                            submit a composed message
#   GET  /connections                        list configured interface connections
#   POST /connections                        save updated connection list order
#   GET  /connections/new                    new connection form
#   POST /connections/new                    create a new interface connection
#   GET  /connections/{id}                   edit an existing connection
#   POST /connections/{id}                   update a connection
#   POST /connections/{id}/delete            delete a connection
#   GET  /connections/{id}/gmail-authorize   start Gmail OAuth flow
#   GET  /gmail-callback                     OAuth redirect handler; stores refresh token
#   GET  /activity                           activity log page
#   GET  /api/conversation/{id}              conversation record JSON
#   GET  /api/conversation/{id}/detail       full conversation + messages JSON
#   GET  /api/events/stream                  SSE: new activity log events
#   POST /api/conversation/{id}/send         send a reply to a specific conversation
#   GET  /conversation/{id}                  conversation detail page
#
# Related modules:
#   - app/config.py        -- cfg (host, port, data_dir)
#   - app/database.py      -- all DB read/write operations
#   - app/poller.py        -- start() / stop() for the background polling thread
#   - app/kc_client.py     -- KoreChat API calls
#   - app/interfaces/      -- adapter registry and type-specific adapters
# ====================================================================================================
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_endpoint_manifest
from KoreCommon.service_app import register_suite_config_js
from KoreCommon.service_app import register_ui_elements_assets
from app import crypto, database as db, kc_client, poller, queue_manager
from app.config import cfg
from app.interfaces.common.registry import REGISTRY, build_adapter

logger = logging.getLogger(__name__)

_MISSING_KC_POLICIES = {"abort", "recreate"}


class MissingKoreChatError(RuntimeError):
    pass


def _parse_id_list(value: str) -> list[str]:
    return [part.strip() for part in value.replace(",", "\n").splitlines() if part.strip()]


def _ids_to_text(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if str(item).strip())
    if isinstance(value, str):
        return value
    return ""


def _store_classic_email_config(config: dict, form: dict, *, preserve_passwords: bool = False) -> None:
    for key in (
        "incoming_protocol", "incoming_host", "incoming_port", "incoming_username", "incoming_mailbox",
        "outgoing_host", "outgoing_port", "outgoing_username", "outgoing_from", "outgoing_security",
        "receive_poll_interval", "send_poll_interval",
    ):
        config[key] = form[key]
    for key in ("incoming_password", "outgoing_password"):
        if form[key] or not preserve_passwords:
            config[key] = crypto.encrypt(form[key]) if form[key] else ""
    config["import_recent_read"] = form.get("import_recent_read") == "on"


def _reset_connection_timing(iface_id: int, config: dict) -> None:
    receive_interval = int(
        config.get("receive_poll_interval", config.get("poll_interval", cfg.get("poll_interval", 60)))
    )
    send_interval    = int(config.get("send_poll_interval", cfg.get("event_poll_interval", 1.0)))
    poller.reset_poll_timing(iface_id, receive_interval, send_interval)

_TEMPLATES = Path(
    os.environ.get(
        "KORE_KORECOMMS_TEMPLATES_DIR",
        str(Path(__file__).resolve().parents[2] / "KoreUI" / "KoreComms" / "templates"),
    )
).resolve()
_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[2] / "KoreUI" / "UIElements" / "assets"),
    )
).resolve()
templates = Jinja2Templates(directory=str(_TEMPLATES))

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    queue_manager.bootstrap()
    poller.start()
    yield
    poller.stop()

app = FastAPI(title="KoreComms", lifespan=lifespan)
register_endpoint_manifest(app, service_key="korecomms", service_label="KoreComms")
register_suite_config_js(app)
register_ui_elements_assets(app, _UI_ELEMENTS_ASSETS)


# ---------------------------------------------------------------------------
# Template context helper
# ---------------------------------------------------------------------------

def _ctx(**extra) -> dict:
    return dict(extra)


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------


@app.get("/status")
def status():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# KoreComms REST API - outbound trigger
# ---------------------------------------------------------------------------


class SendRequest(BaseModel):
    interface_id:          int
    recipient:             str = ""
    distribution_list_id:  int | None = None
    subject:               str
    content:               str


class DeliveryBindingRequest(BaseModel):
    chat_name:            str
    connection:           str
    recipient:            str = ""
    distribution_list_id: int | None = None
    subject:              str = "KoreComms report"
    enabled:              bool = True


class DistributionListRequest(BaseModel):
    interface_id: int
    name:         str
    description:  str = ""


class DistributionListMemberRequest(BaseModel):
    email:        str
    display_name: str = ""


class DistributionListUpdateRequest(BaseModel):
    name:        str
    description: str = ""


def _api_send_one(iface_row: dict, recipient: str, subject: str, content: str) -> dict:
    adapter = build_adapter(iface_row)
    routing = adapter.send_new(recipient, subject, content)

    ext_thread_id = routing["external_thread_id"]
    ext_msg_id    = routing.get("external_message_id", ext_thread_id)
    local_conv_id = db.conversation_create(
        interface_id       = iface_row["id"],
        external_thread_id = ext_thread_id,
        korechat_id        = subject,
    )
    local_conv = db.conversation_get(local_conv_id)
    assert local_conv is not None
    kc_conv = _resolve_kc_conversation(local_conv, if_missing="recreate")
    kc_msg = kc_client.append_message(
        kc_conversation_id = kc_conv["id"],
        direction          = "outbound",
        content            = content,
        sender_display     = "KoreComms",
    )
    kc_client.mark_message_sent(kc_msg["id"])
    db.external_message_create(local_conv_id, ext_msg_id, "outbound")
    db.log_activity("send_new", f"via {iface_row['name']} to {recipient}")
    return {
        "recipient":          recipient,
        "conversation_id":    local_conv_id,
        "conversation_name":  local_conv["chat_name"],
        "kc_conversation_id": kc_conv["id"],
    }


@app.post("/api/send")
def api_send(req: SendRequest):
    """Initiate a brand-new outbound message on a specified interface."""
    iface_row = db.interface_get(req.interface_id)
    if iface_row is None:
        raise HTTPException(404, "Interface not found")
    recipient = req.recipient.strip()
    if bool(recipient) == bool(req.distribution_list_id):
        raise HTTPException(400, "Specify exactly one of recipient or distribution_list_id")
    if recipient:
        return _api_send_one(iface_row, recipient, req.subject, req.content)

    list_row = db.distribution_list_get(int(req.distribution_list_id))
    if list_row is None:
        raise HTTPException(404, "Distribution list not found")
    if list_row["interface_id"] != iface_row["id"]:
        raise HTTPException(409, "Distribution list belongs to a different connection")
    members = db.distribution_list_members(int(req.distribution_list_id))
    if not members:
        raise HTTPException(409, "Distribution list has no members")
    deliveries = [_api_send_one(iface_row, member["email"], req.subject, req.content) for member in members]
    return {"distribution_list": list_row["name"], "deliveries": deliveries}


@app.get("/api/distribution-lists")
def api_distribution_list_list(interface_id: int | None = None):
    return db.distribution_list_list(interface_id)


@app.post("/api/distribution-lists", status_code=201)
def api_distribution_list_create(req: DistributionListRequest):
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    if db.interface_get(req.interface_id) is None:
        raise HTTPException(404, "Interface not found")
    try:
        list_id = db.distribution_list_create(req.interface_id, req.name, req.description)
    except Exception as exc:
        raise HTTPException(409, f"Could not create distribution list: {exc}") from exc
    return db.distribution_list_get(list_id)


@app.get("/api/distribution-lists/{list_id}")
def api_distribution_list_get(list_id: int):
    list_row = db.distribution_list_get(list_id)
    if list_row is None:
        raise HTTPException(404, "Distribution list not found")
    return {**list_row, "members": db.distribution_list_members(list_id)}


@app.post("/api/distribution-lists/{list_id}/members", status_code=201)
def api_distribution_list_member_add(list_id: int, req: DistributionListMemberRequest):
    if db.distribution_list_get(list_id) is None:
        raise HTTPException(404, "Distribution list not found")
    if not req.email.strip():
        raise HTTPException(400, "email is required")
    try:
        member_id = db.distribution_list_member_add(list_id, req.email, req.display_name)
    except Exception as exc:
        raise HTTPException(409, f"Could not add distribution-list member: {exc}") from exc
    return next(member for member in db.distribution_list_members(list_id) if member["id"] == member_id)


@app.delete("/api/distribution-lists/{list_id}/members/{member_id}", status_code=204)
def api_distribution_list_member_delete(list_id: int, member_id: int):
    db.distribution_list_member_delete(list_id, member_id)
    return Response(status_code=204)


@app.delete("/api/distribution-lists/{list_id}", status_code=204)
def api_distribution_list_delete(list_id: int):
    db.distribution_list_delete(list_id)
    return Response(status_code=204)


@app.patch("/api/distribution-lists/{list_id}")
def api_distribution_list_update(list_id: int, req: DistributionListUpdateRequest):
    if db.distribution_list_get(list_id) is None:
        raise HTTPException(404, "Distribution list not found")
    if not req.name.strip():
        raise HTTPException(400, "name is required")
    db.distribution_list_update(list_id, req.name, req.description)
    return db.distribution_list_get(list_id)


@app.patch("/api/distribution-lists/{list_id}/members/{member_id}")
def api_distribution_list_member_update(list_id: int, member_id: int, req: DistributionListMemberRequest):
    if db.distribution_list_get(list_id) is None:
        raise HTTPException(404, "Distribution list not found")
    if not req.email.strip():
        raise HTTPException(400, "email is required")
    db.distribution_list_member_update(list_id, member_id, req.email, req.display_name)
    return next(member for member in db.distribution_list_members(list_id) if member["id"] == member_id)


@app.post("/api/delivery-bindings")
def api_delivery_bind(req: DeliveryBindingRequest):
    chat_name       = req.chat_name.strip()
    recipient       = req.recipient.strip()
    connection_name = req.connection.strip()
    if not chat_name:
        raise HTTPException(400, "chat_name is required")
    if bool(recipient) == bool(req.distribution_list_id):
        raise HTTPException(400, "Specify exactly one of recipient or distribution_list_id")
    if not connection_name:
        raise HTTPException(400, "connection is required")

    interfaces = db.interface_list()
    exact       = [row for row in interfaces if row["name"].casefold() == connection_name.casefold()]
    matches     = exact or [
        row for row in interfaces
        if connection_name.casefold() in row["name"].casefold()
    ]
    if not matches:
        raise HTTPException(404, f"No connection matches '{connection_name}'")
    if len(matches) > 1:
        names = ", ".join(row["name"] for row in matches)
        raise HTTPException(409, f"Connection '{connection_name}' is ambiguous: {names}")
    iface = matches[0]
    if not iface["enabled"]:
        raise HTTPException(409, "Connection is disabled")
    list_row = None
    if req.distribution_list_id:
        list_row = db.distribution_list_get(req.distribution_list_id)
        if list_row is None:
            raise HTTPException(404, "Distribution list not found")
        if list_row["interface_id"] != iface["id"]:
            raise HTTPException(409, "Distribution list belongs to a different connection")
    subject = req.subject.strip() or "KoreComms report"
    try:
        conv_id, _ = db.conversation_bind_delivery(
            interface_id    = iface["id"],
            chat_name       = chat_name,
            korechat_id     = req.subject.strip(),
            recipient       = recipient,
            list_id         = req.distribution_list_id,
            subject         = subject,
            enabled         = req.enabled,
            activity_detail = f"conv via={iface['name']} to={list_row['name'] if list_row else recipient}",
        )
    except ValueError:
        raise HTTPException(409, "Chat is already bound to a different connection") from None
    try:
        kc_conv = kc_client.find_or_create_conversation(
            external_id  = chat_name,
            channel_type = iface["type"],
            subject      = subject,
        )
        kc_client.set_conversation_channel_type(kc_conv["id"], iface["type"])
        db.conversation_set_kc_id(conv_id, kc_conv["id"])
    except RuntimeError as exc:
        raise HTTPException(503, f"Could not attach delivery to KoreChat: {exc}") from exc
    return {
        "conversation_id":      conv_id,
        "chat_name":            chat_name,
        "connection":           iface["name"],
        "recipient":            recipient or None,
        "distribution_list_id": req.distribution_list_id,
        "enabled":              req.enabled,
    }


# ---------------------------------------------------------------------------
# WebUI - home (conversation list)
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def ui_home(request: Request, offset: int = 0):
    conversations = db.conversation_list(limit=50, offset=offset)
    return templates.TemplateResponse(
        request,
        "home.html",
        _ctx(conversations=conversations, offset=offset),
    )


# ---------------------------------------------------------------------------
# WebUI - compose / inject manual message
# ---------------------------------------------------------------------------


@app.get("/compose", response_class=HTMLResponse)
def ui_compose_form(request: Request):
    return templates.TemplateResponse(request, "compose.html", _ctx())


@app.post("/compose")
def ui_compose_submit(
    request: Request,
    sender:  str = Form(...),
    korechat_id: str = Form(...),
    content: str = Form(...),
):
    manual = db.interface_get_manual()
    ext_thread_id = f"manual:{uuid.uuid4()}"

    local_conv_id = db.conversation_create(
        interface_id=manual["id"],
        external_thread_id=ext_thread_id,
        korechat_id=korechat_id,
    )
    local_conv = db.conversation_get(local_conv_id)
    assert local_conv is not None
    kc_conv = _resolve_kc_conversation(local_conv, if_missing="recreate")
    ext_msg_id = f"{ext_thread_id}:0"
    db.external_message_create(local_conv_id, ext_msg_id, "inbound", sender)

    kc_client.append_message(kc_conv["id"], "inbound", content, sender_display=sender)
    db.log_activity("injected", f"Manual inject from {sender}")
    return RedirectResponse(f"/conversation/{local_conv_id}", status_code=303)


# ---------------------------------------------------------------------------
# WebUI - connections (interface management)
# ---------------------------------------------------------------------------


@app.get("/connections", response_class=HTMLResponse)
def ui_connections(request: Request):
    interfaces = db.interface_list()
    available_types = [t for t in REGISTRY if t != "manual"]
    return templates.TemplateResponse(
        request,
        "connections.html",
        _ctx(
            interfaces=interfaces,
            available_types=available_types,
            timings=poller.get_poll_timing(),
        ),
    )


@app.get("/api/connections/timing")
def api_connections_timing():
    """Return the remaining inbound and outbound poll times for each interface."""
    return {"timings": poller.get_poll_timing()}


@app.get("/distribution-lists", response_class=HTMLResponse)
def ui_distribution_lists(request: Request, list_id: int | None = None):
    lists = db.distribution_list_list()
    selected_id = list_id or (lists[0]["id"] if lists else None)
    selected = db.distribution_list_get(selected_id) if selected_id else None
    if selected is not None:
        selected["members"] = db.distribution_list_members(selected_id)
    return templates.TemplateResponse(
        request,
        "distribution_lists.html",
        _ctx(interfaces=db.interface_list(), lists=lists, selected=selected),
    )


@app.post("/distribution-lists")
def ui_distribution_list_create(
    interface_id: int = Form(...),
    name: str = Form(...),
    description: str = Form(default=""),
):
    if not name.strip():
        raise HTTPException(400, "List name is required")
    try:
        db.distribution_list_create(interface_id, name, description)
    except Exception as exc:
        raise HTTPException(409, f"Could not create distribution list: {exc}") from exc
    return RedirectResponse("/distribution-lists", status_code=303)


@app.post("/distribution-lists/{list_id}")
def ui_distribution_list_update(
    list_id: int,
    name: str = Form(...),
    description: str = Form(default=""),
):
    if not name.strip():
        raise HTTPException(400, "List name is required")
    db.distribution_list_update(list_id, name, description)
    return RedirectResponse(f"/distribution-lists?list_id={list_id}", status_code=303)


@app.post("/distribution-lists/{list_id}/members")
def ui_distribution_list_member_add(
    list_id: int,
    email: str = Form(...),
    display_name: str = Form(default=""),
):
    if not email.strip():
        raise HTTPException(400, "Email address is required")
    try:
        db.distribution_list_member_add(list_id, email, display_name)
    except Exception as exc:
        raise HTTPException(409, f"Could not add member: {exc}") from exc
    return RedirectResponse(f"/distribution-lists?list_id={list_id}", status_code=303)


@app.post("/distribution-lists/{list_id}/members/{member_id}")
def ui_distribution_list_member_update(
    list_id: int,
    member_id: int,
    email: str = Form(...),
    display_name: str = Form(default=""),
):
    if not email.strip():
        raise HTTPException(400, "Email address is required")
    db.distribution_list_member_update(list_id, member_id, email, display_name)
    return RedirectResponse(f"/distribution-lists?list_id={list_id}", status_code=303)


@app.post("/distribution-lists/{list_id}/members/{member_id}/delete")
def ui_distribution_list_member_delete(list_id: int, member_id: int):
    db.distribution_list_member_delete(list_id, member_id)
    return RedirectResponse(f"/distribution-lists?list_id={list_id}", status_code=303)


@app.post("/distribution-lists/{list_id}/delete")
def ui_distribution_list_delete(list_id: int):
    db.distribution_list_delete(list_id)
    return RedirectResponse("/distribution-lists", status_code=303)


@app.get("/connections/new", response_class=HTMLResponse)
def ui_connections_new(request: Request, type: str = "gmail"):
    if type not in REGISTRY or type == "manual":
        raise HTTPException(400, "Unsupported interface type")
    return templates.TemplateResponse(
        request,
        "connection_edit.html",
        _ctx(iface=None, iface_type=type, poll_interval=cfg.get("poll_interval", 60)),
    )


@app.post("/connections/new")
def ui_connections_create(
    request:       Request,
    iface_type:    str = Form(...),
    name:          str = Form(...),
    bot_token:     str = Form(default=""),
    channel_ids:   str = Form(default=""),
    client_id:     str = Form(default=""),
    client_secret: str = Form(default=""),
    poll_interval: int = Form(default=60),
    incoming_protocol: str = Form(default="imap"),
    incoming_host: str = Form(default=""),
    incoming_port: int = Form(default=993),
    incoming_username: str = Form(default=""),
    incoming_password: str = Form(default=""),
    incoming_mailbox: str = Form(default="INBOX"),
    import_recent_read: str = Form(default="off"),
    outgoing_host: str = Form(default=""),
    outgoing_port: int = Form(default=587),
    outgoing_username: str = Form(default=""),
    outgoing_password: str = Form(default=""),
    outgoing_from: str = Form(default=""),
    outgoing_security: str = Form(default="starttls"),
    receive_poll_interval: int = Form(default=60),
    send_poll_interval: int = Form(default=10),
):
    if iface_type not in REGISTRY or iface_type == "manual":
        raise HTTPException(400, "Unsupported interface type")
    config: dict = {"poll_interval": poll_interval}
    if iface_type == "gmail":
        config["client_id"]     = crypto.encrypt(client_id)     if client_id     else ""
        config["client_secret"] = crypto.encrypt(client_secret) if client_secret else ""
    if iface_type == "discord":
        config["bot_token"] = crypto.encrypt(bot_token) if bot_token else ""
        config["channel_ids"] = _parse_id_list(channel_ids)
    if iface_type == "classic_email":
        _store_classic_email_config(config, locals())
    iface_id = db.interface_create(iface_type, name, config)
    _reset_connection_timing(iface_id, config)
    return RedirectResponse(f"/connections/{iface_id}", status_code=303)


@app.get("/connections/{iface_id}", response_class=HTMLResponse)
def ui_connections_edit(request: Request, iface_id: int):
    iface = db.interface_get(iface_id)
    if iface is None:
        raise HTTPException(404, "Interface not found")
    config = json.loads(iface.get("config_json", "{}"))
    return templates.TemplateResponse(
        request,
        "connection_edit.html",
        _ctx(
            iface          = iface,
            iface_type     = iface["type"],
            config         = config,
            poll_interval  = config.get("poll_interval", cfg.get("poll_interval", 60)),
            discord_channel_ids_text = _ids_to_text(config.get("channel_ids", [])),
            gmail_authorized = bool(config.get("refresh_token")),
            receive_poll_interval = config.get("receive_poll_interval", config.get("poll_interval", 60)),
            send_poll_interval = config.get("send_poll_interval", 10),
        ),
    )


@app.post("/connections/{iface_id}")
def ui_connections_update(
    request:       Request,
    iface_id:      int,
    name:          str = Form(...),
    bot_token:     str = Form(default=""),
    channel_ids:   str = Form(default=""),
    client_id:     str = Form(default=""),
    client_secret: str = Form(default=""),
    poll_interval: int = Form(default=60),
    incoming_protocol: str = Form(default="imap"),
    incoming_host: str = Form(default=""),
    incoming_port: int = Form(default=993),
    incoming_username: str = Form(default=""),
    incoming_password: str = Form(default=""),
    incoming_mailbox: str = Form(default="INBOX"),
    import_recent_read: str = Form(default="off"),
    outgoing_host: str = Form(default=""),
    outgoing_port: int = Form(default=587),
    outgoing_username: str = Form(default=""),
    outgoing_password: str = Form(default=""),
    outgoing_from: str = Form(default=""),
    outgoing_security: str = Form(default="starttls"),
    receive_poll_interval: int = Form(default=60),
    send_poll_interval: int = Form(default=10),
    enabled:       str = Form(default="off"),
):
    iface = db.interface_get(iface_id)
    if iface is None:
        raise HTTPException(404, "Interface not found")
    existing = json.loads(iface.get("config_json", "{}"))
    existing["poll_interval"] = poll_interval
    if iface["type"] == "gmail":
        if client_id:
            existing["client_id"]     = crypto.encrypt(client_id)
        if client_secret:
            existing["client_secret"] = crypto.encrypt(client_secret)
    if iface["type"] == "discord":
        if bot_token:
            existing["bot_token"] = crypto.encrypt(bot_token)
        existing["channel_ids"] = _parse_id_list(channel_ids)
    if iface["type"] == "classic_email":
        _store_classic_email_config(existing, locals(), preserve_passwords=True)
    db.interface_update(iface_id, name, existing, enabled == "on")
    _reset_connection_timing(iface_id, existing)
    return RedirectResponse("/connections", status_code=303)


@app.post("/connections/{iface_id}/delete")
def ui_connections_delete(request: Request, iface_id: int):
    iface = db.interface_get(iface_id)
    if iface is None:
        raise HTTPException(404, "Interface not found")
    if iface["type"] == "manual":
        raise HTTPException(400, "Cannot delete the Manual interface")
    db.interface_delete(iface_id)
    return RedirectResponse("/connections", status_code=303)


# ---------------------------------------------------------------------------
# Gmail OAuth flow
# ---------------------------------------------------------------------------


def _gmail_redirect_uri(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/gmail-callback"


@app.get("/connections/{iface_id}/gmail-authorize")
def ui_gmail_authorize(request: Request, iface_id: int):
    from app.interfaces.gmail import build_auth_url

    iface = db.interface_get(iface_id)
    if iface is None or iface["type"] != "gmail":
        raise HTTPException(404, "Gmail interface not found")
    config = json.loads(iface.get("config_json", "{}"))
    client_id     = crypto.decrypt(config["client_id"])     if config.get("client_id")     else ""
    client_secret = crypto.decrypt(config["client_secret"]) if config.get("client_secret") else ""
    if not client_id or not client_secret:
        raise HTTPException(400, "Add client_id and client_secret first")
    redirect_uri = _gmail_redirect_uri(request)
    auth_url = build_auth_url(client_id, client_secret, redirect_uri, str(iface_id))
    return RedirectResponse(auth_url)


@app.get("/gmail-callback", response_class=HTMLResponse)
def ui_gmail_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return templates.TemplateResponse(
            request,
            "connections.html",
            _ctx(
                interfaces      = db.interface_list(),
                available_types = [t for t in REGISTRY if t != "manual"],
                flash           = f"OAuth error: {error}",
            ),
        )
    from app.interfaces.gmail import exchange_code

    iface_id = int(state)
    iface = db.interface_get(iface_id)
    if iface is None:
        raise HTTPException(404)
    config = json.loads(iface.get("config_json", "{}"))
    client_id     = crypto.decrypt(config["client_id"])     if config.get("client_id")     else ""
    client_secret = crypto.decrypt(config["client_secret"]) if config.get("client_secret") else ""
    redirect_uri  = _gmail_redirect_uri(request)
    refresh_token = exchange_code(client_id, client_secret, redirect_uri, code)
    config["refresh_token"] = crypto.encrypt(refresh_token)
    db.interface_update(iface_id, iface["name"], config, bool(iface["enabled"]))
    return RedirectResponse(f"/connections/{iface_id}", status_code=303)


# ---------------------------------------------------------------------------
# WebUI - activity log
# ---------------------------------------------------------------------------


@app.get("/activity", response_class=HTMLResponse)
def ui_activity(request: Request):
    entries = db.activity_list(limit=200)
    return templates.TemplateResponse(request, "activity_log.html", _ctx(entries=entries))


# ---------------------------------------------------------------------------
# WebUI - per-conversation chat view
# ---------------------------------------------------------------------------


def _normalize_kc_messages(kc_messages: list[dict]) -> list[dict]:
    """Map KC message fields to the shape the chat template expects."""
    return [
        {
            "id":          m["id"],
            "direction":   m["direction"],
            "content":     m["content"],
            "sender":      m.get("sender_display", ""),
            "received_at": m.get("created_at", ""),
            "status":      m.get("status", ""),
        }
        for m in kc_messages
    ]


def _normalize_kc_events(kc_events: list[dict]) -> list[dict]:
    return [
        {
            "id": e.get("id"),
            "event_type": e.get("event_type", ""),
            "status": e.get("status", ""),
            "claimed_by": e.get("claimed_by") or "",
            "claimed_at": e.get("claimed_at") or "",
            "created_at": e.get("created_at") or "",
            "completed_at": e.get("completed_at") or "",
            "priority": e.get("priority", 0),
        }
        for e in kc_events
    ]


def _conversation_name_for(conv: dict) -> str:
    name = (conv.get("chat_name") or "").strip()
    if name:
        return name
    fallback = conv.get("external_thread_id") or f"kccomms:{conv['id']}"
    db.conversation_set_name(conv["id"], fallback)
    conv["chat_name"] = fallback
    return fallback


def _missing_kc_policy(policy: str | None = None) -> str:
    resolved = (policy or cfg.get("missing_kc_conversation_policy", "recreate") or "recreate").strip().lower()
    if resolved not in _MISSING_KC_POLICIES:
        resolved = "recreate"
    return resolved


def _resolve_kc_conversation(conv: dict, *, if_missing: str | None = None) -> dict:
    conversation_name = _conversation_name_for(conv)
    channel_type = conv.get("interface_type", "manual")
    korechat_id = conv.get("korechat_id") or ""

    kc_conv = kc_client.find_conversation_by_external_id(conversation_name)
    if kc_conv is None:
        db.conversation_set_kc_id(conv["id"], None)
        conv["kc_chat_id"] = None
        policy = _missing_kc_policy(if_missing)
        if policy == "abort":
            raise MissingKoreChatError(
                f"KoreChat record missing for local conversation '{conversation_name}'"
            )
        kc_conv = kc_client.create_conversation(
            external_id=conversation_name,
            channel_type=channel_type,
            subject=korechat_id,
        )
        logger.info(
            "Created KC conversation %d for local conv %d via name %s",
            kc_conv["id"],
            conv["id"],
            conversation_name,
        )

    kc_id = kc_conv.get("id")
    if conv.get("kc_chat_id") != kc_id:
        db.conversation_set_kc_id(conv["id"], kc_id)
        conv["kc_chat_id"] = kc_id
    return kc_conv


def _get_conversation_detail_payload(conv: dict) -> dict:
    conversation_name = _conversation_name_for(conv)
    payload = {
        "conversation": conv,
        "conversation_name": conversation_name,
        "missing_kc_policy": _missing_kc_policy(),
    }

    kc_conv = kc_client.find_conversation_by_external_id(conversation_name)
    if kc_conv is None:
        db.conversation_set_kc_id(conv["id"], None)
        conv["kc_chat_id"] = None
        return {
            **payload,
            "kc_conversation": None,
            "kc_status": "missing",
            "thread": [],
            "events": [],
            "input_history": [],
        }

    if conv.get("kc_chat_id") != kc_conv.get("id"):
        db.conversation_set_kc_id(conv["id"], kc_conv.get("id"))
        conv["kc_chat_id"] = kc_conv.get("id")

    kc_detail = kc_client.get_conversation_detail(kc_conv["id"])
    if kc_detail is None:
        return {**payload, "kc_conversation": None, "kc_status": "missing", "thread": [], "events": [], "input_history": []}

    kc_conversation = kc_detail.get("conversation", {})
    return {
        **payload,
        "kc_conversation": kc_conversation,
        "kc_status": "linked",
        "thread": _normalize_kc_messages(kc_detail.get("messages", [])),
        "events": _normalize_kc_events(kc_detail.get("events", [])),
        "input_history": kc_conversation.get("input_history", []),
    }


def _ensure_kc_conv(conv: dict) -> int:
    """Return the current KC conversation id for a local conversation name."""
    return _resolve_kc_conversation(conv)["id"]


@app.get("/api/conversation/{conv_id}")
def api_conversation(conv_id: int):
    conv = db.conversation_get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    try:
        detail = _get_conversation_detail_payload(conv)
    except RuntimeError as exc:
        raise HTTPException(502, f"KoreChat unavailable: {exc}")
    return {
        "conversation": detail.get("kc_conversation") or conv,
        "thread": detail.get("thread", []),
        "kc_status": detail.get("kc_status", "missing"),
    }


@app.get("/api/conversation/{conv_id}/detail")
def api_conversation_detail(conv_id: int):
    conv = db.conversation_get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    try:
        return _get_conversation_detail_payload(conv)
    except RuntimeError as exc:
        raise HTTPException(502, f"KoreChat unavailable: {exc}")


def _stream_kc_events():
    url = cfg["korechat_url"].rstrip("/") + "/stream"
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            while True:
                chunk = resp.readline()
                if not chunk:
                    break
                yield chunk
    except urllib.error.HTTPError as exc:
        logger.warning("KC stream HTTP error: %s", exc)
        yield b"event: error\ndata: {\"type\":\"stream_error\"}\n\n"
    except OSError as exc:
        logger.warning("KC stream connection error: %s", exc)
        yield b"event: error\ndata: {\"type\":\"stream_error\"}\n\n"


@app.get("/api/events/stream")
def api_events_stream():
    return StreamingResponse(
        _stream_kc_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class ConversationSendRequest(BaseModel):
    content: str
    if_missing: str | None = None


@app.post("/api/conversation/{conv_id}/send")
def api_conversation_send(conv_id: int, req: ConversationSendRequest):
    conv = db.conversation_get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")

    content = req.content.strip()
    if not content:
        raise HTTPException(400, "content cannot be empty")

    try:
        kc_conv = _resolve_kc_conversation(conv, if_missing=req.if_missing)
        kc_conv_id = kc_conv["id"]
        kc_client.append_message(kc_conv_id, "inbound", content, "Human")
        history = kc_client.append_input_history(kc_conv_id, content)
        detail = _get_conversation_detail_payload(db.conversation_get(conv_id) or conv)
    except MissingKoreChatError as exc:
        raise HTTPException(409, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, f"KoreChat unavailable: {exc}")

    db.log_activity("injected", f"Human reply in conv={conv_id}")
    return JSONResponse({"ok": True, "input_history": history, "detail": detail})


@app.get("/conversation/{conv_id}", response_class=HTMLResponse)
def ui_conversation(request: Request, conv_id: int):
    conv = db.conversation_get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")

    iface  = db.interface_get(conv["interface_id"])
    thread: list[dict] = []
    kc_data: dict = {}

    try:
        detail = _get_conversation_detail_payload(conv)
        kc_data = detail.get("kc_conversation") or {}
        thread  = detail.get("thread", [])
        conv["chat_name"] = detail.get("conversation_name", conv.get("chat_name"))
    except RuntimeError as exc:
        logger.warning("KC fetch failed for conv %d: %s", conv_id, exc)

    return templates.TemplateResponse(
        request,
        "chat.html",
        _ctx(
            conv=conv,
            iface=iface,
            thread=thread,
            kc_conv=kc_data,
            distribution_lists=db.distribution_list_list(conv["interface_id"]),
        ),
    )


@app.post("/conversation/{conv_id}/delivery")
def ui_conversation_delivery(
    conv_id: int,
    recipient: str = Form(default=""),
    distribution_list_id: int = Form(default=0),
    subject: str = Form(default=""),
    enabled: str = Form(default="off"),
):
    conv = db.conversation_get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    recipient = recipient.strip()
    list_id = distribution_list_id or None
    if enabled == "on" and bool(recipient) == bool(list_id):
        raise HTTPException(400, "Choose exactly one recipient or distribution list when delivery is enabled")
    if list_id:
        list_row = db.distribution_list_get(list_id)
        if list_row is None or list_row["interface_id"] != conv["interface_id"]:
            raise HTTPException(400, "Distribution list does not belong to this connection")
    db.conversation_set_delivery(conv_id, recipient, list_id, subject.strip(), enabled == "on")
    destination = db.distribution_list_get(list_id)["name"] if list_id else recipient or "(disabled)"
    db.log_activity("delivery_bound", f"conv={conv_id} to={destination}")
    return RedirectResponse(f"/conversation/{conv_id}", status_code=303)


@app.post("/conversation/{conv_id}/delete")
def ui_conversation_delete(request: Request, conv_id: int):
    conv = db.conversation_get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    kc_conv_id = None
    conversation_name = _conversation_name_for(conv)
    kc_conv = None
    try:
        kc_conv = kc_client.find_conversation_by_external_id(conversation_name)
    except RuntimeError as exc:
        logger.warning("KC lookup failed during delete for conv %d: %s", conv_id, exc)
    if kc_conv is not None:
        kc_conv_id = kc_conv.get("id")
        try:
            kc_client.delete_conversation(kc_conv_id)
        except RuntimeError as exc:
            logger.warning("KC delete failed for conv %d: %s", conv_id, exc)
    db.conversation_delete(conv_id)
    db.log_activity("deleted", f"conv={conv_id} name={conversation_name} kc_conv={kc_conv_id}")
    return RedirectResponse("/", status_code=303)


@app.post("/conversation/{conv_id}/send")
def ui_conversation_send(
    request: Request,
    conv_id: int,
    content: str = Form(...),
    if_missing: str = Form(default=""),
):
    """Human sends a message in an existing conversation - forwarded to KC."""
    conv = db.conversation_get(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    if not content.strip():
        return RedirectResponse(f"/conversation/{conv_id}", status_code=303)

    try:
        text = content.strip()
        kc_conv = _resolve_kc_conversation(conv, if_missing=if_missing or None)
        kc_conv_id = kc_conv["id"]
        kc_client.append_message(kc_conv_id, "inbound", text, "Human")
        kc_client.append_input_history(kc_conv_id, text)
    except MissingKoreChatError as exc:
        raise HTTPException(409, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, f"KoreChat unavailable: {exc}")

    db.log_activity("injected", f"Human reply in conv={conv_id}")
    return RedirectResponse(f"/conversation/{conv_id}", status_code=303)
