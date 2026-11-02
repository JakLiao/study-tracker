"""/api/analytics/review."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from ..services import analytics_service

router = APIRouter(prefix="/api", tags=["analytics"])

# 统计周期的取值。前后端共用同一套词 (current/last/month/last_month),
# 页面上的"本周/上周/本月/上月"按钮与这里一一对应。
PERIOD_PATTERN = "^(current|last|month|last_month)$"

# month 参数: "YYYY-MM", 只对 last_month 周期有意义 (看哪个月)。
# 不传 = 上个月。走 Query 的 pattern 校验, 传坏了直接 422, 不进业务逻辑。
MONTH_PATTERN = r"^\d{4}-\d{2}$"


@router.get("/analytics/review")
def review_analytics(
    period: str = Query("current", pattern=PERIOD_PATTERN),
    month: str | None = Query(None, pattern=MONTH_PATTERN, description="YYYY-MM, 仅 last_month 周期有效"),
) -> dict:
    today = datetime.now(timezone.utc).astimezone().date()
    result = analytics_service.build_period_analytics(period, today, month)
    return result


@router.get("/analytics/review-time")
def review_time_stats(
    period: str = Query("current", pattern=PERIOD_PATTERN),
    month: str | None = Query(None, pattern=MONTH_PATTERN, description="YYYY-MM, 仅 last_month 周期有效"),
) -> dict:
    """每日各科作业用时 (按上学日口径)。

    独立于 /analytics/review 的原因: 那条接口在样本 < 5 时抛
    SampleTooSmallError (4011), 复盘页会整块降级成"样本不足"。
    但"每天各科要写多久"是**另一件事** —— 一周只完成 3 条作业时,
    偏差表算不出来, 用时统计照样有结论。所以不能挂在同一条接口后面,
    否则会被 4011 一起藏起来。
    """
    today = datetime.now(timezone.utc).astimezone().date()
    return analytics_service.build_daily_time_stats(period, today, month)
