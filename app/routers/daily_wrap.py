"""/api/daily-wrap (M3 日结: 晚间三问 + 每晚小结)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..models import DailyReflectionSave
from ..services import assignment_service, daily_wrap_service

router = APIRouter(prefix="/api", tags=["daily-wrap"])


@router.get("/daily-wrap")
def get_daily_wrap(date: str = Query(...)) -> dict:
    """某一天的小结. 没有三问记录时 reflection=null, 不算错误."""
    d = assignment_service.parse_date_param(date)
    return daily_wrap_service.get_daily_wrap(d)


@router.post("/daily-wrap")
def save_daily_wrap(body: DailyReflectionSave) -> dict:
    """提交/更新晚间三问, 返回最新小结 (前端直接整卡重绘)."""
    return daily_wrap_service.save_reflection(body)
