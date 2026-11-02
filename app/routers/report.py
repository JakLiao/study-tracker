"""/api/weekly-report (M6) · /api/monthly-report (2026-11-02)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ..errors import BadDateFormatError
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


# ====== 本月课后月报接口 (2026-11-02) ======
#
# 月报是周报的月度版本: 多两件事 —— 月内按周对比、环比上月**同期**。
#
# month 参数接受两种写法:
#   "2026-11"     → 该月 (推荐)
#   "2026-11-02"  → 该月 (按天解析, 取所在月)
# 两者都表示"看哪个月", 不关心具体哪一天。

def _parse_month(s: str) -> date:
    """把 month 参数解析成该月的某一天 (取 1 号, 语义上最干净)。"""
    s = (s or "").strip()
    if not s:
        raise BadDateFormatError("月份不能为空")
    # "2026-11" → "2026-11-01"
    if len(s) == 7 and s[4] == "-":
        s = s + "-01"
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError) as e:
        raise BadDateFormatError(f"月份格式错误: {s} (应为 YYYY-MM 或 YYYY-MM-DD)") from e


@router.get("/monthly-report")
def monthly_report(month: str | None = Query(None, description="YYYY-MM, 缺省当月")) -> dict:
    anchor = _parse_month(month) if month else _today()
    return report_service.build_monthly_report(anchor)
