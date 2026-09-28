"""FastAPI 入口."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config
from .db import init_db, apply_wal_pragma
from .errors import AppError, error_payload
from .routers import analytics as analytics_router
from .routers import assignments as assignments_router
from .routers import board as board_router
from .routers import export as export_router
from .routers import health as health_router
from .routers import parse as parse_router
from .routers import settings as settings_router
from .seed import seed_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("study.main")


def create_app() -> FastAPI:
    app = FastAPI(
        title="study-tracker",
        version=__version__,
        description="小学生作业跟踪平台 v1",
    )

    # 启动期: 初始化 DB + WAL + 默认设置
    init_db()
    apply_wal_pragma()
    seed_settings()

    if not config.ai_configured():
        logger.warning(
            "MINIMAX_API_KEY 未配置, AI 解析不可用, 但手动录入 / 看板 / 打卡 / 复盘 / 导出全部可用"
        )
    else:
        logger.info(
            "AI 模型: %s @ %s (timeout=%dss)",
            config.MINIMAX_MODEL,
            config.MINIMAX_API_BASE,
            config.MINIMAX_TIMEOUT,
        )

    # 路由
    app.include_router(health_router.router)
    app.include_router(parse_router.router)
    app.include_router(assignments_router.router)
    app.include_router(board_router.router)
    app.include_router(analytics_router.router)
    app.include_router(settings_router.router)
    app.include_router(export_router.router)

    # 错误统一响应: AppError
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # pydantic 校验失败 -> 4003
        return JSONResponse(
            status_code=400,
            content=error_payload("4003", f"请求参数校验失败: {exc.errors()[0].get('msg', '')[:200]}"),
        )

    @app.exception_handler(Exception)
    async def fallback_handler(request: Request, exc: Exception):
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_payload("5000", f"服务异常: {type(exc).__name__}"),
        )

    # 静态资源
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()