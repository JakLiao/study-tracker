"""/api/training-logs: 训练打卡 (M4)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..models import TrainingLogSave
from ..services import assignment_service, training_service

router = APIRouter(prefix="/api", tags=["training"])


@router.get("/training-logs")
def list_training_logs(date: str = Query(...)) -> dict:
    """某天的打卡记录. 前端用它预填"改打卡"弹窗."""
    d = assignment_service.parse_date_param(date)
    return {"date": d.isoformat(), "items": training_service.list_logs(d)}


@router.post("/training-logs")
def save_training_log(body: TrainingLogSave) -> dict:
    """打卡即完成: 同时写训练记录并把任务标记为 done."""
    return training_service.save_log(body)
