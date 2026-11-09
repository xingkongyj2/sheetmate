"""FastAPI 应用入口。

启动：在 backend 目录执行 `python -m sheetmate`（或 `uvicorn sheetmate.app:app --reload`）。
前端 `npm run build` 之后，产物（frontend/dist）由这里一起托管，单端口访问。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sheetmate import __version__
from sheetmate.agent.llm import LLMError
from sheetmate.api import auth, conversations, customers, files, recipes, runs, scripts
from sheetmate.api import settings as settings_api
from sheetmate.config import get_llm_settings, settings
from sheetmate.services.errors import ServiceError
from sheetmate.services.seed import bootstrap


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 初始化里会同步跑一遍示例流程（内部用 asyncio.run），放到线程里执行，避免和服务的事件循环冲突
    await asyncio.to_thread(bootstrap)
    from sheetmate.services.chat import recover_interrupted_executions

    await asyncio.to_thread(recover_interrupted_executions)
    yield
    from sheetmate.services.conversations import conversation_workspaces
    from sheetmate.services.runs import run_workspaces
    from sheetmate.services.jobs import jobs

    await jobs.shutdown()
    conversation_workspaces.close_all()
    run_workspaces.close_all()


def _error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": {"type": kind, "message": message}})


def create_app() -> FastAPI:
    app = FastAPI(title="SheetMate", version=__version__, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return _error(exc.status_code, exc.error_type, exc.message)

    @app.exception_handler(LLMError)
    async def _llm_error(_: Request, exc: LLMError) -> JSONResponse:
        return _error(400, "llm", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", [])[1:])
        return _error(422, "validation", f"参数不正确：{field} {first.get('msg', '')}".strip())

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return _error(400, "bad_request", str(exc))

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "llm_configured": get_llm_settings().configured}

    for module in (auth, settings_api, customers, conversations, files, recipes, runs, scripts):
        app.include_router(module.router)
    app.include_router(customers.memories_router)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """托管前端构建产物；所有非 /api 的路径都回到 index.html（单页应用路由）。"""
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.exists():
        return
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = (dist / path).resolve()
        if path and target.is_file() and dist.resolve() in target.parents:
            return FileResponse(target)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
