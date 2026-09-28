"""Pydantic DTO (v2 风格).

DTO 与 DB 行解耦, 用 row_to_dto / dto_to_dict 转换.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config


Subject = Literal["语文", "数学", "英语"]
Status = Literal["todo", "doing", "done", "skipped"]
Confidence = Literal["high", "medium", "low"]


# ====== Parse 接口 ======
class ParseRequest(BaseModel):
    raw_text: Annotated[str, Field(min_length=1, max_length=10000)]


class ParsedItem(BaseModel):
    """AI 解析出的单条任务. 入库前家长可编辑."""

    model_config = ConfigDict(extra="ignore")

    subject: Subject
    task_name: Annotated[str, Field(min_length=1, max_length=200)]
    due_date: date | None = None
    deadline_time: datetime | None = None
    is_optional: bool = False
    est_minutes: int | None = Field(default=None, ge=1, le=180)
    task_type: str | None = Field(default=None, max_length=50)
    source_span: Annotated[str, Field(min_length=1, max_length=500)]
    confidence: Confidence = "medium"

    @field_validator("source_span")
    @classmethod
    def _strip_source_span(cls, v: str) -> str:
        v2 = v.strip()
        if not v2:
            raise ValueError("source_span 不能为空")
        return v2


class ParseResponse(BaseModel):
    batch_id: int
    items: list[ParsedItem]
    model_version: str


# ====== Batch 入库 ======
class AssignmentCreate(BaseModel):
    """入库时由家长确认/编辑后的单条. id 可选."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    subject: Subject
    task_name: Annotated[str, Field(min_length=1, max_length=200)]
    due_date: date
    deadline_time: datetime | None = None
    is_optional: bool = False
    est_minutes: Annotated[int, Field(ge=1, le=180)]
    task_type: str | None = Field(default=None, max_length=50)
    source_span: Annotated[str, Field(min_length=1, max_length=500)] = ""
    confidence: Confidence = "medium"


class BatchCreateRequest(BaseModel):
    batch_id: int | None = None  # 解析批次 ID (可有可无, 手动添加可为空)
    items: list[AssignmentCreate] = Field(min_length=1)


class BatchCreateResponse(BaseModel):
    created_ids: list[int]


# ====== Assignment DTO (看板 / 列表) ======
class AssignmentDTO(BaseModel):
    id: int
    subject: Subject
    task_name: str
    due_date: date
    deadline_time: datetime | None
    is_optional: bool
    est_minutes: int
    actual_minutes: int | None
    status: Status
    task_type: str | None
    source_span: str
    confidence: Confidence
    batch_id: int | None
    created_at: datetime
    updated_at: datetime


# ====== 看板 ======
class TodayBoard(BaseModel):
    date: date
    is_late_night: bool  # 凌晨 0-5 点
    progress: dict[str, int]  # {"done": N, "total": M}
    by_subject: dict[str, list[AssignmentDTO]]
    optional_items: list[AssignmentDTO]
    upcoming_deadlines: list[AssignmentDTO]  # 本周内非今日到期的
    has_data: bool


# ====== 状态切换 ======
class StatusPatch(BaseModel):
    status: Status


class CompletionPatch(BaseModel):
    actual_minutes: Annotated[int, Field(ge=0, le=240)]


class OptionalPatch(BaseModel):
    is_optional: bool


class AssignmentEdit(BaseModel):
    task_name: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    est_minutes: Annotated[int | None, Field(default=None, ge=1, le=180)] = None
    task_type: str | None = Field(default=None, max_length=50)
    due_date: date | None = None
    deadline_time: datetime | None = None
    subject: Subject | None = None


# ====== Settings ======
class SettingItem(BaseModel):
    key: str
    value: str


class SettingsResponse(BaseModel):
    items: list[SettingItem]


class SettingPatch(BaseModel):
    value: str


# ====== Analytics ======
class WeeklyAnalytics(BaseModel):
    week: Literal["current", "last"]
    week_start: date
    week_end: date
    sample_size: int
    note: str | None = None
    by_type: list[dict] = []
    top_overestimate: list[dict] = []
    top_underestimate: list[dict] = []


# ====== Health ======
class HealthResponse(BaseModel):
    status: Literal["ok"]
    ai_reachable: bool
    ai_model: str
    db_ok: bool


# ====== Helpers ======
def row_to_assignment_dto(row) -> AssignmentDTO:
    """sqlite3.Row -> AssignmentDTO. 字段对齐."""
    return AssignmentDTO(
        id=row["id"],
        subject=row["subject"],
        task_name=row["task_name"],
        due_date=date.fromisoformat(row["due_date"]),
        deadline_time=(
            datetime.fromisoformat(row["deadline_time"])
            if row["deadline_time"]
            else None
        ),
        is_optional=bool(row["is_optional"]),
        est_minutes=row["est_minutes"],
        actual_minutes=row["actual_minutes"],
        status=row["status"],
        task_type=row["task_type"],
        source_span=row["source_span"] or "",
        confidence=row["confidence"],
        batch_id=row["batch_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def validate_subject(value: str) -> Subject:
    if value not in config.SUBJECTS:
        raise ValueError(f"subject must be one of {config.SUBJECTS}")
    return value  # type: ignore[return-value]


def validate_status(value: str) -> Status:
    if value not in config.STATUSES:
        raise ValueError(f"status must be one of {config.STATUSES}")
    return value  # type: ignore[return-value]