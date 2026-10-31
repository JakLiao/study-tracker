"""看板 API.

/board/today  今天的看板 (默认, 无参数)
/board?date=   指定日期的看板, 供看板顶部日期 Tab 切换
/board/dates   最近 N 个"有作业"的日期 + 轻量统计, 供 Tab 渲染
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from ..services import assignment_service

router = APIRouter(prefix="/api", tags=["board"])


def _local_today():
    # 用本地日期而非 UTC。toISOString/getdate 取 UTC 时, 在凌晨时段
    # 会退到前一天, 家长早上打开看板会看到"今天没有作业"。
    return datetime.now(timezone.utc).astimezone().date()


def _serialize(board: dict) -> dict:
    return {
        "date": board["date"].isoformat(),
        "label": board["label"],
        "is_today": board["is_today"],
        "is_late_night": board["is_late_night"],
        "progress": board["progress"],
        "by_subject": {
            s: [x.model_dump(mode="json") for x in lst]
            for s, lst in board["by_subject"].items()
        },
        "by_kind": {
            k: [x.model_dump(mode="json") for x in lst]
            for k, lst in board["by_kind"].items()
        },
        "optional_items": [x.model_dump(mode="json") for x in board["optional_items"]],
        "upcoming_deadlines": [x.model_dump(mode="json") for x in board["upcoming_deadlines"]],
        "overdue_items": [x.model_dump(mode="json") for x in board["overdue_items"]],
        # M4 训练打卡: 按 assignment_id 给出当天的打卡记录, 前端在训练任务行
        # 上直接显示"练了多久 / 几组 × 几次", 不必每行再请求一次。
        "has_data": board["has_data"],
    }


@router.get("/board/today")
def board_today() -> dict:
    today = _local_today()
    return _serialize(assignment_service.build_board_for(today, today))


@router.get("/board")
def board_by_date(date: str = Query(...)) -> dict:
    d = assignment_service.parse_date_param(date)
    return _serialize(assignment_service.build_board_for(d, _local_today()))


@router.get("/board/dates")
def board_dates(limit: int = Query(3, ge=1, le=30)) -> dict:
    """最近 N 个日期, 最新在前, "今天"永远在第一位。

    今天没有作业时也会返回一张 0/0 的空卡 (has_data=false): 前端默认
    高亮第一张, 若第一张是"昨天", 深色底会被读成"今天是空白的一天"。
    其余日期只取"有作业"的 —— 长假期间连续多天无作业, 按日历日取
    会让 3 个 Tab 里有一半是空的。
    """
    return {"dates": assignment_service.list_recent_dates(limit, _local_today())}
