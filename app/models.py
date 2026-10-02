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


class SkippedAssignment(BaseModel):
    """被去重跳过的作业摘要, 用于告知家长跳过了什么."""
    task_name: str
    subject: Subject
    due_date: date
    reason: str


class BatchCreateResponse(BaseModel):
    created_ids: list[int]
    skipped: list[SkippedAssignment] = Field(default_factory=list)


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
    """局部更新一条已入库的作业.

    所有字段默认 None, 且必须区分"没传"与"传了 null"两种情况:
      没传               -> 保持原值 (PATCH 语义)
      deadline_time=null -> 清空截止时间
      task_type=null     -> 清空类型
    靠 model_fields_set 判定字段是否出现在请求里, 见 assignment_service.edit_assignment。
    """

    model_config = ConfigDict(extra="ignore")

    task_name: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    est_minutes: Annotated[int | None, Field(default=None, ge=1, le=180)] = None
    task_type: str | None = Field(default=None, max_length=50)
    due_date: date | None = None
    deadline_time: datetime | None = None
    subject: Subject | None = None
    # 2026-10-02: 编辑弹窗补上「是否选做」, 与手动添加对齐。
    # 类型必须是 bool | None 而不是 bool: 这个模型靠 model_fields_set
    # 区分"没传"与"传了", 而 is_optional 是 False 才是合法值 —— 写成
    # bool 会让 False 与"没传"都落进同一个默认值, 取消选做就永远发不出去。
    # 之前只能走行内按钮的 /optional 专用接口, 这里补齐后两个弹窗字段一致。
    is_optional: bool | None = None

    @field_validator("task_name")
    @classmethod
    def _strip_task_name(cls, v: str | None) -> str | None:
        # 去首尾空白后再判非空: 否则 "   " 能过 min_length=1 入库,
        # 看板只显示一个空标签, 家长完全看不出这条作业叫什么。
        if v is None:
            return None
        v2 = v.strip()
        if not v2:
            raise ValueError("task_name 不能为空")
        return v2


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