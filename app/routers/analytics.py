"""/api/analytics/weekly."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from ..services import analytics_service

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytics/weekly")
def weekly_analytics(week: str = Query("current", pattern="^(current|last)$")) -> dict:
    today = datetime.now(timezone.utc).astimezone().date()
    result = analytics_service.build_weekly_analytics(week, today)
    return result


@router.get("/analytics/weekly-time")
def weekly_time_stats(week: str = Query("current", pattern="^(current|last)$")) -> dict:
    """每日各科作业用时 (按上学日口径)。

    独立于 /analytics/weekly 的原因: 那条接口在样本 < 5 时抛
    SampleTooSmallError (4011), 复盘页会整块降级成"样本不足"。
    但"每天各科要写多久"是**另一件事** —— 一周只完成 3 条作业时,
    偏差表算不出来, 用时统计照样有结论。所以不能挂在同一条接口后面,
    否则会被 4011 一起藏起来。
    """
    today = datetime.now(timezone.utc).astimezone().date()
    return analytics_service.build_daily_time_stats(week, today)