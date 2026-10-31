"""统一错误响应 + 业务异常.

错误响应格式:
    {"error": {"code": "STR_CODE", "message": "..."}}

错误码前缀:
    1xxx 客户端输入错误
    4xxx 业务规则违反
    5xxx 服务端异常
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class AppError(HTTPException):
    """业务异常基类.

    子类覆盖 code + message; FastAPI handler 捕获后输出统一格式.
    """

    code: str = "5000"
    default_message: str = "服务异常"

    def __init__(self, message: str | None = None, *, status_code: int | None = None):
        self.message = message or self.default_message
        super().__init__(
            status_code=status_code or self._default_status(),
            detail={"code": self.code, "message": self.message},
        )

    @classmethod
    def _default_status(cls) -> int:
        # 1xxx -> 400, 4xxx -> 400, 5xxx -> 500
        prefix = cls.code[:1]
        if prefix == "5":
            return status.HTTP_500_INTERNAL_SERVER_ERROR
        return status.HTTP_400_BAD_REQUEST


# ====== 1xxx 客户端输入错误 ======
class EmptyTextError(AppError):
    code = "4001"
    default_message = "文本不能为空"


class TextTooLongError(AppError):
    code = "4002"
    default_message = "文本过长, 最多 10000 字符"


class MissingFieldError(AppError):
    code = "4003"
    default_message = "必填字段缺失"


class FieldOutOfRangeError(AppError):
    code = "4004"
    default_message = "字段值越界"


class BatchNotFoundError(AppError):
    code = "4005"
    default_message = "批次不存在"


class BadDateFormatError(AppError):
    code = "4006"
    default_message = "日期格式错误, 应为 YYYY-MM-DD"


class BadStatusError(AppError):
    code = "4007"
    default_message = "状态值非法"


class AssignmentNotFoundError(AppError):
    code = "4008"
    default_message = "作业不存在"


class MinutesOutOfRangeError(AppError):
    code = "4009"
    default_message = "分钟数越界"


class DeleteDoneForbiddenError(AppError):
    code = "4010"
    default_message = "已完成作业禁止删除, 请先改回 todo"


class SampleTooSmallError(AppError):
    code = "4011"
    default_message = "样本不足, 至少需要 5 条已完成作业"


class BadSettingKeyError(AppError):
    code = "4012"
    default_message = "设置项 key 不存在"


class BadSettingValueError(AppError):
    code = "4013"
    default_message = "设置项 value 格式错误"


class ProjectNotFoundError(AppError):
    code = "4014"
    default_message = "项目不存在"


class BadTaskKindError(AppError):
    code = "4015"
    default_message = "任务类型非法"


class ScheduleConflictError(AppError):
    code = "4016"
    default_message = "课表时段冲突"


class ScheduleSlotNotFoundError(AppError):
    code = "4017"
    default_message = "课表时段不存在"


class PlanItemNotFoundError(AppError):
    code = "4018"
    default_message = "周计划项不存在"


# ====== 5xxx 服务端异常 ======
class AITimeoutError(AppError):
    code = "5001"
    default_message = "AI 解析超时, 请重试或手动添加"


class AIFormatError(AppError):
    code = "5002"
    default_message = "AI 返回格式无法解析"


class DBWriteError(AppError):
    code = "5003"
    default_message = "数据库写入失败"


class ExportError(AppError):
    code = "5004"
    default_message = "数据导出失败"


def error_payload(code: str, message: str) -> dict[str, Any]:
    """生成错误响应 dict (用于非异常路径, 如解析为空数组)."""
    return {"error": {"code": code, "message": message}}