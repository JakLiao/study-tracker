"""Pydantic DTO (v2 风格).

DTO 与 DB 行解耦, 用 row_to_dto / dto_to_dict 转换.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import config


Subject = Literal["语文", "数学", "英语"]
Status = Literal["todo", "doing", "done", "skipped"]
Confidence = Literal["high", "medium", "low"]
TaskKind = Literal["subject", "training", "habit"]
ProjectStatus = Literal["active", "paused", "archived"]


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
    """入库时由家长确认/编辑后的单条. id 可选.

    M1 统一任务池: task_kind=subject 时 subject 必填; 培训/习惯用 project_id 归类,
    subject 必须为空 —— 否则 DTO/看板会出现"钢琴任务挂在语文下"的假分类.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    subject: Subject | None = None
    task_kind: TaskKind = "subject"
    project_id: int | None = None
    task_name: Annotated[str, Field(min_length=1, max_length=200)]
    due_date: date
    deadline_time: datetime | None = None
    is_optional: bool = False
    est_minutes: Annotated[int, Field(ge=1, le=180)]
    task_type: str | None = Field(default=None, max_length=50)
    source_span: Annotated[str, Field(min_length=1, max_length=500)] = ""
    confidence: Confidence = "medium"

    @model_validator(mode="after")
    def _check_kind_subject(self) -> "AssignmentCreate":
        if self.task_kind == "subject" and self.subject is None:
            raise ValueError("学科作业必须选择科目")
        if self.task_kind != "subject" and self.subject is not None:
            raise ValueError("培训/习惯任务不能挂科目")
        return self


class BatchCreateRequest(BaseModel):
    batch_id: int | None = None  # 解析批次 ID (可有可无, 手动添加可为空)
    items: list[AssignmentCreate] = Field(min_length=1)


class SkippedAssignment(BaseModel):
    """被去重跳过的作业摘要, 用于告知家长跳过了什么."""
    task_name: str
    subject: Subject | None = None
    task_kind: TaskKind = "subject"
    due_date: date
    reason: str


class BatchCreateResponse(BaseModel):
    created_ids: list[int]
    skipped: list[SkippedAssignment] = Field(default_factory=list)


# ====== Assignment DTO (看板 / 列表) ======
class AssignmentDTO(BaseModel):
    id: int
    subject: Subject | None
    task_kind: TaskKind
    project_id: int | None
    project_name: str | None
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
    # 已过截止时间且仍未收尾 (M2 执行台). 计算字段, 不落库: 判定依赖
    # "现在", 存进 DTO 才能让看板与列表用同一口径显示逾期标记。
    is_overdue: bool = False


# ====== 看板 ======
class TodayBoard(BaseModel):
    date: date
    is_late_night: bool  # 凌晨 0-5 点
    progress: dict[str, int]  # {"done": N, "total": M}
    by_subject: dict[str, list[AssignmentDTO]]
    by_kind: dict[str, list[AssignmentDTO]]  # 培训 / 习惯
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
    # M1: 任务归属可从"学科作业"改成"培训/习惯", 也可以换项目。
    task_kind: TaskKind | None = None
    project_id: int | None = None
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


# ====== 项目 / 统一任务池 (M1) ======
class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Annotated[str, Field(min_length=1, max_length=60)]
    project_type: Annotated[str, Field(default="custom", max_length=30)] = "custom"
    coach_name: Annotated[str | None, Field(default=None, max_length=60)] = None
    schedule_note: Annotated[str | None, Field(default=None, max_length=200)] = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v2 = v.strip()
        if not v2:
            raise ValueError("项目名不能为空")
        return v2


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Annotated[str | None, Field(default=None, min_length=1, max_length=60)] = None
    project_type: Annotated[str | None, Field(default=None, max_length=30)] = None
    coach_name: Annotated[str | None, Field(default=None, max_length=60)] = None
    schedule_note: Annotated[str | None, Field(default=None, max_length=200)] = None
    status: ProjectStatus | None = None


class ProjectDTO(BaseModel):
    id: int
    name: str
    project_type: str
    coach_name: str | None
    schedule_note: str | None
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ProjectTemplateTask(BaseModel):
    task_name: str
    est_minutes: int
    task_type: str | None = None


class ProjectTemplate(BaseModel):
    key: str
    label: str
    task_kind: TaskKind
    project_type: str
    default_name: str
    tasks: list[ProjectTemplateTask]


class ProjectTemplatesResponse(BaseModel):
    items: list[ProjectTemplate]


class QuickAddRequest(BaseModel):
    """一键添加: 模板任务 / 常做任务点一下直接入库.

    due_date 默认由前端传今天; est_minutes 缺省时取模板值或 15。
    """

    model_config = ConfigDict(extra="ignore")

    task_name: Annotated[str, Field(min_length=1, max_length=200)]
    due_date: date
    task_kind: TaskKind = "subject"
    subject: Subject | None = None
    project_id: int | None = None
    est_minutes: Annotated[int, Field(default=15, ge=1, le=180)] = 15
    task_type: str | None = Field(default=None, max_length=50)
    is_optional: bool = False

    @model_validator(mode="after")
    def _check_kind_subject(self) -> "QuickAddRequest":
        if self.task_kind == "subject" and self.subject is None:
            raise ValueError("学科作业必须选择科目")
        if self.task_kind != "subject" and self.subject is not None:
            raise ValueError("培训/习惯任务不能挂科目")
        return self


class TrainingLogSave(BaseModel):
    """训练打卡: 时长 + 组数/次数 + 曲目段落.

    三个量化字段里只有时长必填 —— 打卡的核心问题是"练没练、练了多久",
    组数和段落是加分项。强制全部填满会让家长在晚上嫌麻烦而直接放弃,
    30 秒打卡的设计初衷就是不能有第二个必填项。
    """

    model_config = ConfigDict(extra="ignore")

    assignment_id: int
    duration_minutes: Annotated[int, Field(ge=0, le=240)]
    # 0 组/0 次没有意义, 但留 0 作为合法值可以让"今天只做了热身"这类
    # 记录成立; 真正的空值走 None (没填就不传)。
    sets: Annotated[int | None, Field(default=None, ge=0, le=100)] = None
    reps: Annotated[int | None, Field(default=None, ge=0, le=1000)] = None
    segment: Annotated[str | None, Field(default=None, max_length=60)] = None
    note: Annotated[str | None, Field(default=None, max_length=200)] = None

    @field_validator("segment", "note")
    @classmethod
    def _strip_text(cls, v: str | None) -> str | None:
        # 空串/纯空白一律落成 None: 前端口径是"没填就不传", 但输入框
        # 清空后可能送来 "", 直接入库会留下"有段落但内容是空"的脏数据。
        if v is None:
            return None
        v2 = v.strip()
        return v2 or None


def _norm_hhmm(value: str) -> str:
    """把 "16:30" / "16:30:00" 统一成 "16:30", 并校验是合法时刻."""
    try:
        parsed = time.fromisoformat(str(value).strip())
    except (ValueError, TypeError) as e:
        raise ValueError("时间格式应为 HH:MM") from e
    return parsed.strftime("%H:%M")


def _norm_weekdays(value: list[int]) -> list[int]:
    """星期取值去重排序并限定 0-6 (0=周一). 空列表视为非法."""
    try:
        nums = sorted({int(v) for v in value})
    except (ValueError, TypeError) as e:
        raise ValueError("星期必须是 0-6 的整数") from e
    if not nums:
        raise ValueError("至少选择一天")
    if nums[0] < 0 or nums[-1] > 6:
        raise ValueError("星期必须是 0-6 的整数")
    return nums


class ScheduleSlotCreate(BaseModel):
    """一条固定上课时段. 时间用 "HH:MM" 文本, 与前端 time 输入框一致."""

    model_config = ConfigDict(extra="ignore")

    project_id: int
    weekday: Annotated[int, Field(ge=0, le=6)]
    start_time: str
    end_time: str
    note: Annotated[str | None, Field(default=None, max_length=60)] = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_time(cls, v: str) -> str:
        return _norm_hhmm(v)

    @field_validator("note")
    @classmethod
    def _strip_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v2 = v.strip()
        return v2 or None

    @model_validator(mode="after")
    def _check_order(self) -> "ScheduleSlotCreate":
        if _norm_hhmm(self.end_time) <= _norm_hhmm(self.start_time):
            raise ValueError("结束时间必须晚于开始时间")
        return self


class ScheduleSlotPatch(BaseModel):
    """课表时段局部更新. 只看 model_fields_set, 区分"没传"与"清空备注"."""

    model_config = ConfigDict(extra="ignore")

    project_id: int | None = None
    weekday: Annotated[int | None, Field(default=None, ge=0, le=6)] = None
    start_time: str | None = None
    end_time: str | None = None
    note: Annotated[str | None, Field(default=None, max_length=60)] = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _check_time(cls, v: str | None) -> str | None:
        return None if v is None else _norm_hhmm(v)

    @field_validator("note")
    @classmethod
    def _strip_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v2 = v.strip()
        return v2 or None


class PlanItemCreate(BaseModel):
    """周计划项: 每周固定要做的任务模板 (星期几 + 任务名 + 预计时长)."""

    model_config = ConfigDict(extra="ignore")

    task_kind: TaskKind = "subject"
    subject: Subject | None = None
    project_id: int | None = None
    task_name: Annotated[str, Field(min_length=1, max_length=200)]
    est_minutes: Annotated[int, Field(ge=1, le=180)] = 15
    weekdays: list[int]

    @field_validator("task_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v2 = v.strip()
        if not v2:
            raise ValueError("任务名不能为空")
        return v2

    @field_validator("weekdays")
    @classmethod
    def _check_weekdays(cls, v: list[int]) -> list[int]:
        return _norm_weekdays(v)

    @model_validator(mode="after")
    def _check_kind_subject(self) -> "PlanItemCreate":
        if self.task_kind == "subject":
            if self.subject is None:
                raise ValueError("学科任务必须选择科目")
            if self.project_id is not None:
                raise ValueError("学科任务不能挂项目")
        else:
            if self.subject is not None:
                raise ValueError("培训/习惯任务不能挂科目")
        return self


class PlanItemPatch(BaseModel):
    """周计划项局部更新. 用 model_fields_set 处理 null 清空."""

    model_config = ConfigDict(extra="ignore")

    task_kind: TaskKind | None = None
    subject: Subject | None = None
    project_id: int | None = None
    task_name: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    est_minutes: Annotated[int | None, Field(default=None, ge=1, le=180)] = None
    weekdays: list[int] | None = None
    active: bool | None = None

    @field_validator("task_name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v2 = v.strip()
        if not v2:
            raise ValueError("任务名不能为空")
        return v2

    @field_validator("weekdays")
    @classmethod
    def _check_weekdays(cls, v: list[int] | None) -> list[int] | None:
        return None if v is None else _norm_weekdays(v)


# ====== 每日三问 (M3 日结) ======
ReflectionDifficulty = Literal["easy", "ok", "hard"]
ReflectionFocus = Literal["good", "ok", "bad"]
ReflectionMood = Literal["good", "ok", "bad"]


class DailyReflectionSave(BaseModel):
    """晚间三问提交.

    档位是固定枚举而不是 1-5 分滑杆: 家长在晚上已经累了, 三个表情点一下
    就完事, 滑杆还要对准刻度。枚举的另一层好处是周报能直接按档位聚合,
    不用先做"分数分桶"。
    """

    model_config = ConfigDict(extra="ignore")

    date: date
    difficulty: ReflectionDifficulty
    # 低年级两问版不填专注; 普通模式也允许留空(孩子说"不知道"时不强迫)。
    focus: ReflectionFocus | None = None
    mood: ReflectionMood
    note: Annotated[str | None, Field(default=None, max_length=200)] = None

    @field_validator("note")
    @classmethod
    def _strip_note(cls, v: str | None) -> str | None:
        # 空串落库会变成"有备注但内容为空", 前端口径是"没写就不传"。
        if v is None:
            return None
        v2 = v.strip()
        return v2 or None


# ====== Analytics ======
class ReviewAnalytics(BaseModel):
    period: Literal["current", "last", "month"]
    period_start: date
    period_end: date
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
def _as_date(value) -> date | None:
    """兼容 PG 原生 date 与旧文本日期."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _as_datetime(value) -> datetime | None:
    """兼容 PG 原生 datetime 与旧 ISO 文本."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def row_to_assignment_dto(row) -> AssignmentDTO:
    """DB 行 (psycopg dict_row) -> AssignmentDTO. 字段对齐."""
    return AssignmentDTO(
        id=row["id"],
        subject=row["subject"],
        task_kind=row["task_kind"],
        project_id=row["project_id"],
        # JOIN 出来的项目名; 单独查 assignments 时没有这一列, 兜成 None。
        project_name=row.get("project_name"),
        task_name=row["task_name"],
        due_date=_as_date(row["due_date"]),
        deadline_time=_as_datetime(row["deadline_time"]),
        is_optional=bool(row["is_optional"]),
        est_minutes=row["est_minutes"],
        actual_minutes=row["actual_minutes"],
        status=row["status"],
        task_type=row["task_type"],
        source_span=row["source_span"] or "",
        confidence=row["confidence"],
        batch_id=row["batch_id"],
        created_at=_as_datetime(row["created_at"]),
        updated_at=_as_datetime(row["updated_at"]),
        is_overdue=_is_overdue(row["deadline_time"], row["status"]),
    )


def _is_overdue(deadline_time, status) -> bool:
    """截止时间已过且任务尚未收尾.

    口径:
      - done (做完了) 与 skipped (今天不做) 都不算逾期 —— 后者是家长
        主动决定不做, 再报红只会重复指责。
      - deadline_time 是本地墙钟时间 (TIMESTAMP 无时区), 因此用本地
        now 比较, 与看板"今天"的算法一致。
    """
    dl = _as_datetime(deadline_time)
    if dl is None or status not in ("todo", "doing"):
        return False
    return dl < datetime.now()


def validate_subject(value: str) -> Subject:
    if value not in config.SUBJECTS:
        raise ValueError(f"subject must be one of {config.SUBJECTS}")
    return value  # type: ignore[return-value]


def validate_status(value: str) -> Status:
    if value not in config.STATUSES:
        raise ValueError(f"status must be one of {config.STATUSES}")
    return value  # type: ignore[return-value]