"""FastAPI 入口."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config
from .db import init_db
from .errors import AppError, error_payload
from .routers import analytics as analytics_router
from .routers import assignments as assignments_router
from .routers import board as board_router
from .routers import daily_wrap as daily_wrap_router
from .routers import export as export_router
from .routers import health as health_router
from .routers import parse as parse_router
from .routers import projects as projects_router
from .routers import report as report_router
from .routers import settings as settings_router
from .routers import training as training_router
from .routers import week_plan as week_plan_router
from .seed import seed_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("study.main")

# 菜单页面对应的路径(2026-09-28)。前端主路由是 hash(#/board), 这份清单
# 只用于同路径兜底, 必须显式枚举而不是写 catch-all —— 否则 /api/notfound
# 之类的真实 404 会被悄悄返回首页, 排障时看到的是首页而不是 404。
#
# "review" 是复盘页(2026-10-30 由 weekly 改名): 该页已不只按周统计,
# 还有"本月", 名字里带 weekly 就与内容不符了。
FRONTEND_PAGES = frozenset({"board", "entry", "list", "week", "review", "settings"})


def create_app() -> FastAPI:
    app = FastAPI(
        title="study-tracker",
        version=__version__,
        description="小学生作业跟踪平台 v1",
    )

    # 启动期: 初始化 DB (建表/迁移) + 默认设置
    init_db()
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
    app.include_router(projects_router.router)
    app.include_router(board_router.router)
    app.include_router(daily_wrap_router.router)
    app.include_router(training_router.router)
    app.include_router(week_plan_router.router)
    app.include_router(report_router.router)
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

    # 静态资源的缓存策略 (2026-10-02)
    #
    # 背景: 改完 app.js 刷新页面看不到变化, 排查发现浏览器拿的是旧副本。
    # 原因是 FastAPI StaticFiles 不发 Cache-Control, 浏览器对"无缓存指令"
    # 的启发式策略是尽量复用本地副本 —— 普通刷新往往根本不重新校验。
    #
    # 用 no-cache 而不是 no-store: 前者允许浏览器存副本, 但每次使用前必须
    # 向服务器校验; 服务器有 ETag, 内容没变就回 304(不传 body)。这样改完
    # 代码刷新必定生效, 又不至于每次都全量重下几百 KB。
    #
    # 刻意不用 immutable: 那等于告诉浏览器"这文件永远不会变, 别再问了"。
    # 本项目的静态资源是固定文件名(app.js 而非 app.<hash>.js), 没有内容
    # 指纹, 一旦发出去就再也更新不了 —— 比现在的缓存问题更严重。
    # 哪天真要上 immutable, 得先把构建改成带 hash 的文件名。
    #
    # 只拦 GET/HEAD: 静态资源的读取本来就只有这两种方法。
    #
    # 刻意按路径而不是"全部 GET"来收窄: 首页和菜单页走 FileResponse
    # (路径是 / 、/board), 静态资源走挂载 (路径是 /static/*), 两者都要覆盖
    # —— 只在 StaticFiles 上加头会漏掉 index.html, 于是出现"页面结构是旧的、
    # 但它引用的 app.js 是新的"这种半新半旧状态, 排障时更难看出问题在哪。
    # 而 /api/* 不该由这里管: 接口响应本来就该实时, 额外挂一条 no-cache
    # 只会给每次请求白加一次校验往返。
    @app.middleware("http")
    async def static_cache_control(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # FRONTEND_PAGES 存的是不带斜杠的裸名("board"), 而 url.path 是
        # "/board" —— 直接 `path in FRONTEND_PAGES` 永远不成立, 菜单页会被
        # 漏掉。lstrip("/") 去掉前导斜杠再比。
        bare = path.lstrip("/")
        is_static = path.startswith("/static/") or path == "/static"
        is_page = path == "/" or bare in FRONTEND_PAGES
        if request.method in ("GET", "HEAD") and (is_static or is_page):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # 静态资源
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(static_dir / "index.html")

        # 菜单各页的独立路径(2026-09-28): 前端用 hash 路由(#/board 等),
        # 这里的同路径兜底只是顺手 —— 手输 /board 而漏掉 # 时不至于 404。
        # 注意放在静态挂载与 API 路由之后, 绝不能写成 catch-all,
        # 否则会把 /api/* 和 /static/* 的 404 也吞成首页, 掩盖真实错误。
        @app.get("/{page}", include_in_schema=False)
        def page_alias(page: str):
            if page in FRONTEND_PAGES:
                return FileResponse(static_dir / "index.html")
            raise HTTPException(status_code=404, detail="Not Found")

    return app


app = create_app()