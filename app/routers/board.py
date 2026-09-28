"""/api/board/today."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from ..services import assignment_service

router = APIRouter(prefix="/api", tags=["board"])


@router.get("/board/today")
def board_today() -> dict:
    today = datetime.now(timezone.utc).astimezone().date()
    board = assignment_service.build_today_board(today)
    # 把 datetime 转 ISO
    return {
        "date": board["date"].isoformat(),
        "is_late_night": board["is_late_night"],
        "progress": board["progress"],
        "by_subject": {s: [x.model_dump(mode="json") for x in lst] for s, lst in board["by_subject"].items()},
        "optional_items": [x.model_dump(mode="json") for x in board["optional_items"]],
        "upcoming_deadlines": [x.model_dump(mode="json") for x in board["upcoming_deadlines"]],
        "has_data": board["has_data"],
    }