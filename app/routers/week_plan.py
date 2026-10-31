"""/api 周课表 + 周计划 (M5)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..models import (
    PlanItemCreate,
    PlanItemPatch,
    ScheduleSlotCreate,
    ScheduleSlotPatch,
)
from ..services import schedule_service, week_plan_service
from ..services.assignment_service import parse_date_param

router = APIRouter(prefix="/api", tags=["week-plan"])


# ====== 固定上课时段 ======
@router.get("/schedule-slots")
def list_schedule_slots() -> dict:
    return {"items": schedule_service.list_slots()}


@router.post("/schedule-slots")
def create_schedule_slot(body: ScheduleSlotCreate) -> dict:
    return schedule_service.create_slot(body)


@router.patch("/schedule-slots/{slot_id}")
def patch_schedule_slot(slot_id: int, body: ScheduleSlotPatch) -> dict:
    return schedule_service.update_slot(slot_id, body)


@router.delete("/schedule-slots/{slot_id}")
def delete_schedule_slot(slot_id: int) -> dict:
    return schedule_service.delete_slot(slot_id)


# ====== 周计划项 ======
@router.get("/plan-items")
def list_plan_items(include_inactive: bool = Query(False)) -> dict:
    return {"items": week_plan_service.list_plan_items(include_inactive)}


@router.post("/plan-items")
def create_plan_item(body: PlanItemCreate) -> dict:
    return week_plan_service.create_plan_item(body)


@router.patch("/plan-items/{item_id}")
def patch_plan_item(item_id: int, body: PlanItemPatch) -> dict:
    return week_plan_service.update_plan_item(item_id, body)


@router.delete("/plan-items/{item_id}")
def delete_plan_item(item_id: int) -> dict:
    return week_plan_service.delete_plan_item(item_id)


# ====== 一周视图 / 一键生成 ======
@router.get("/week-plan")
def get_week_plan(date: str = Query(...)) -> dict:
    return week_plan_service.get_week_plan(parse_date_param(date))


@router.post("/week-plan/generate")
def generate_week_plan(date: str = Query(...)) -> dict:
    return week_plan_service.generate_week(parse_date_param(date))
