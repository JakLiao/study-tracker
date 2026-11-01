"""/api/kid/* (孩子端: 星星罐 / 勋章 / 家长 PIN).

路径与请求体严格照规范 §5.2 的表格; 响应模型全部来自 app/models.py,
字段名一字不改, 前端按名字取值。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ..errors import BadDateFormatError
from ..models import (
    KidMedalsResponse,
    KidStarsResponse,
    PinSetRequest,
    PinStatusResponse,
    PinVerifyRequest,
    PinVerifyResponse,
    StarGrantRequest,
    StarGrantResponse,
    StarRedeemRequest,
    StarRedeemResponse,
)
from ..services import kid_service

router = APIRouter(prefix="/api", tags=["kid"])


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except (ValueError, AttributeError) as e:
        raise BadDateFormatError(f"日期格式错误: {raw}") from e


@router.get("/kid/stars", response_model=KidStarsResponse)
def get_stars(date: str | None = Query(None, description="YYYY-MM-DD, 默认今天")) -> KidStarsResponse:
    """星星罐: 余额 / 今日获得 / 连续天数 / 账本 / 本周概览."""
    target = _parse_date(date) if date else None
    return KidStarsResponse.model_validate(kid_service.get_stars(target))


@router.post("/kid/stars/grant", response_model=StarGrantResponse)
def grant_stars(body: StarGrantRequest) -> StarGrantResponse:
    """家长手动奖励 (1~20 星)."""
    return StarGrantResponse.model_validate(kid_service.grant_stars(body.stars, body.reason))


@router.post("/kid/stars/redeem", response_model=StarRedeemResponse)
def redeem_stars(body: StarRedeemRequest) -> StarRedeemResponse:
    """家长手动扣除 (1~999 星). 余额不够返回业务错误."""
    return StarRedeemResponse.model_validate(kid_service.redeem_stars(body.stars, body.reason))


@router.get("/kid/medals", response_model=KidMedalsResponse)
def get_medals() -> KidMedalsResponse:
    """勋章墙: 已获得的 items 与还没拿到的 locked."""
    return KidMedalsResponse.model_validate(kid_service.get_medals())


@router.get("/kid/pin/status", response_model=PinStatusResponse)
def pin_status() -> PinStatusResponse:
    """是否已设置家长 PIN (前端只问"要不要弹门禁")."""
    return PinStatusResponse.model_validate(kid_service.pin_status())


@router.post("/kid/pin", response_model=PinVerifyResponse)
def set_pin(body: PinSetRequest) -> PinVerifyResponse:
    """设置/覆盖家长 PIN (4 位数字, 后端只存 PBKDF2 哈希)."""
    return PinVerifyResponse.model_validate(kid_service.set_pin(body.pin))


@router.post("/kid/pin/verify", response_model=PinVerifyResponse)
def verify_pin(body: PinVerifyRequest) -> PinVerifyResponse:
    """校验家长 PIN. 没设过 -> ok=false; 输错 -> 4011."""
    return PinVerifyResponse.model_validate(kid_service.verify_pin(body.pin))


@router.delete("/kid/pin", response_model=PinVerifyResponse)
def clear_pin() -> PinVerifyResponse:
    """清除家长 PIN."""
    return PinVerifyResponse.model_validate(kid_service.clear_pin())
