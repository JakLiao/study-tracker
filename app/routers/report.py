"""/api/weekly-report (M6)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..services import report_service
from ..services.assignment_service import parse_date_param
from ..services.report_service import _today

# ====== 本周课后周报接口 (M6) ======
#
# 一个入口: 周内任意一天当锚点, 返回该周(周一~周日)的完成情况、
# 用时分布与卡点归因。前端复盘页的"本周课后周报"直接吃这份数据。

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/weekly-report")
def weekly_report(date: str | None = Query(None, description="周内任意一天, 默认今天")) -> dict:
    anchor = parse_date_param(date) if date else _today()
    return report_service.build_weekly_report(anchor)
