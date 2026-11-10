from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from KoreCommon.service_app import register_suite_shell_routes
from KoreCommon.suite_paths import get_suite_urls_map
from .processor import (
    EXPANSION_PROMPT, MAX_TEXT_LENGTH, SYSTEM_PROMPT,
    CollateRequest, ExpandStatementRequest, ProcessRequest, ProcessingError, TextProcessor,
)


ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / "KoreUI" / "KoreData" / "KoreTex"
templates = Jinja2Templates(directory=str(UI / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        app.state.processor = TextProcessor(client, get_suite_urls_map().get("koreagent", ""))
        yield


app = FastAPI(title="KoreTex", lifespan=lifespan)
register_suite_shell_routes(
    app, service_key="koretex", service_label="KoreTex",
    ui_elements_assets_dir=ROOT / "KoreUI" / "UIElements" / "assets",
)
app.mount("/static/tex", StaticFiles(directory=str(UI / "static")), name="tex-static")


def get_processor(request: Request) -> TextProcessor:
    return request.app.state.processor


@app.get("/status")
async def status():
    return {"service": "KoreTex", "status": "ready", "mode": "preview"}


@app.get("/")
async def root():
    return RedirectResponse("/ui/tex")


@app.get("/ui/tex")
async def text_entry(request: Request):
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={
            "max_text_length": MAX_TEXT_LENGTH,
            "system_prompt": SYSTEM_PROMPT,
            "expansion_prompt": EXPANSION_PROMPT,
        },
    )


@app.get("/api/readiness")
async def readiness(processor: TextProcessor = Depends(get_processor)):
    return (await processor.readiness()).model_dump()


@app.post("/api/process")
async def process(body: ProcessRequest, processor: TextProcessor = Depends(get_processor)):
    try:
        model, result = await processor.process(body.text, system_prompt=body.system_prompt)
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"model": model, "result": result}


@app.post("/api/expand-statement")
async def expand_statement(
    body: ExpandStatementRequest,
    processor: TextProcessor = Depends(get_processor),
):
    try:
        model, result, warnings = await processor.expand_statement(
            body.text,
            body.statement,
            expansion_prompt=body.expansion_prompt,
        )
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"model": model, "result": result, "warnings": warnings}


@app.post("/api/collate-statements")
async def collate_statements(body: CollateRequest, processor: TextProcessor = Depends(get_processor)):
    try:
        model, result, warnings = await processor.collate_statements(body.text, body.statements)
    except ProcessingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"model": model, "result": result, "warnings": warnings}
