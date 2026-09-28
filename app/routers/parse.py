"""/api/parse 路由 (AI 解析通知原文)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from .. import config
from ..ai_client import call_minimax, parse_ai_response
from ..errors import AIFormatError, EmptyTextError, TextTooLongError
from ..models import ParsedItem
from ..prompts import build_messages  # noqa: F401  便于 monkeypatch 测试
from ..services import assignment_service

router = APIRouter(prefix="/api", tags=["parse"])
logger = logging.getLogger("study.parse")


def _check_text(raw: str) -> str:
    if not raw or not raw.strip():
        raise EmptyTextError()
    if len(raw) > 10000:
        raise TextTooLongError(f"文本 {len(raw)} 字符, 超过 10000 上限")
    return raw


def _failure_envelope(error: dict[str, Any]) -> dict[str, Any]:
    """统一失败响应体. HTTP 仍 200, 前端用 data.ok / data.error 判断."""
    code = error.get("code", "AI_UNKNOWN")
    msg = error.get("message", "AI 解析失败")
    return {
        "ok": False,
        "error": error,
        "batch_id": None,
        "items": error.get("items", []),
        "model_version": config.MINIMAX_MODEL,
        # Key 缺失 -> ai_reachable=False; 其余 (超时/网络/格式) -> ai_reachable=True
        "ai_reachable": code != "AI_KEY_MISSING",
        # 兼容旧前端 fallback (前端可能仍读 message)
        "message": msg,
    }


@router.post("/parse")
def parse_endpoint(payload: dict[str, Any]) -> dict:
    """AI 解析. body = {"raw_text": str}.

    P1 返工 2026-09-28:
    - 成功: {"ok": True, "batch_id", "items", "model_version", "ai_reachable": True}
    - 失败 (HTTP 200, 结构化错误): {"ok": False, "error": {"code", "message", "items": []}, ...}

    Returns:
        {"ok": bool, "batch_id": int|None, "items": [...], "model_version": str, "ai_reachable": bool,
         "error": dict | None, "message": str | None}
    """
    raw = payload.get("raw_text", "")
    raw = _check_text(raw if isinstance(raw, str) else "")

    result = call_minimax(raw)

    # ====== 结构化错误分支 (call_minimax 内部已重试, 不再抛异常) ======
    if isinstance(result, dict) and result.get("ok") is False:
        err = result.get("error") or {"code": "AI_UNKNOWN", "message": "AI 解析失败", "items": []}
        logger.warning("AI parse error: %s - %s", err.get("code"), err.get("message"))
        return _failure_envelope(err)

    # ====== 成功路径 ======
    content = result["content"]
    model = result["model"]

    try:
        items_raw = parse_ai_response(content)
    except AIFormatError as e:
        # content 不可解析 (内部 _extract_json / _coerce_item 仍抛, 兼容)
        logger.warning("AI response parse failed: %s", e.message)
        return _failure_envelope({"code": "AI_FORMAT", "message": e.message, "items": []})

    # 校验 + 转换
    items: list[dict] = []
    for it in items_raw:
        try:
            parsed = ParsedItem.model_validate(it)
            items.append(parsed.model_dump())
        except Exception as e:
            logger.warning("单条解析失败, 跳过: %s", str(e)[:120])
            continue

    # 创建批次 (去重用 hash)
    batch_id, _is_dup = assignment_service.create_parsed_batch(
        raw_text=raw, model_version=model, result_count=len(items)
    )

    return {
        "ok": True,
        "batch_id": batch_id,
        "items": items,
        "model_version": model,
        "ai_reachable": True,
    }