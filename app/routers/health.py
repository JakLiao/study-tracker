"""/api/health."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from .. import config
from ..db import get_conn
from ..models import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok = True
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        db_ok = False
    # ai_reachable: 仅基于 Key 是否配置判断 (不真发请求, 避免每次都打 AI)
    return HealthResponse(
        status="ok",
        ai_reachable=config.ai_configured(),
        ai_model=config.MINIMAX_MODEL,
        db_ok=db_ok,
    )