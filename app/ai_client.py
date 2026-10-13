"""MiniMax API 调用 + JSON 健壮解析.

健壮性:
- 处理 ```json ... ``` 围栏
- 处理前后多余文字
- 处理返回单个对象而非数组 (自动包成数组)
- 处理字段缺失 / 类型不对 (尽力纠正, 不可纠正则抛 5002)
- 处理超时 (call_minimax 内部 1 次重试, 重试用尽返结构化错误, 不抛 5xx)

绝不打印 API Key 内容.

call_minimax 返回协议 (P1 返工: 2026-09-28):
- 成功: {"content": str, "model": str}  (向后兼容老测试 mock)
- 失败 (结构化错误, 不抛异常):
    {
      "ok": False,
      "error": {
        "code": "AI_TIMEOUT" | "AI_NETWORK" | "AI_KEY_MISSING" | "AI_FORMAT",
        "message": "人类可读",
        "items": [],
      },
    }
"""
from __future__ import annotations

import json
import logging
import re
import time as _time
from datetime import datetime, timezone
from typing import Any

import requests

from . import config
from .errors import AIFormatError, AITimeoutError


logger = logging.getLogger("study.ai_client")


# MiniMax 兼容 OpenAI ChatCompletion 协议 (国内站 v1)
# 参考: https://api.minimaxi.com/doc  -- 实际端点以 .env 为准
DEFAULT_CHAT_ENDPOINT = "/v1/text/chatcompletion_v2"


def _endpoint_url() -> str:
    base = config.MINIMAX_API_BASE.rstrip("/")
    return f"{base}{DEFAULT_CHAT_ENDPOINT}"


def _redact(s: str) -> str:
    """脱敏: 把看起来像 Key 的字符串替换掉."""
    return re.sub(r"[A-Za-z0-9_-]{20,}", "***REDACTED***", s)


def call_minimax(raw_text: str) -> dict:
    """调用 MiniMax API 解析通知原文.

    Returns:
        Success: {"content": str, "model": str}   (向后兼容老测试 mock)
        Failure (结构化错误, 不抛异常):
            {"ok": False, "error": {"code": str, "message": str, "items": []}}

    P1 返工 2026-09-28:
    - 网络/超时: 内部 1 次指数退避重试 (+2s), 用尽后返结构化错误, 不抛 5xx
    - Key 缺失: 立即返结构化错误 (code=AI_KEY_MISSING)
    - HTTP 非 200 / JSON 解码失败 / 字段缺失 / content 为空:
        返结构化错误 (code=AI_FORMAT)
    """
    if not config.ai_configured():
        return _err("AI_KEY_MISSING", "AI Key 未配置, 仅手动录入可用")

    from .prompts import build_messages  # 延迟导入, 便于测试时 monkeypatch

    # 注意: M3 不支持 OpenAI 的 response_format={type:json_object} 协议 (实测会忽略且自动包 ```json 围栏),
    # ai_client 的 _strip_fences 已能处理围栏, 这里不传 response_format 让模型自由输出, 靠客户端兜底解析.
    #
    # thinking: 本项目这一环节只是"把老师通知原文拆成结构化作业条目", 不需要多步推理,
    # 而 M3 省略 thinking 时默认开启 adaptive thinking (会额外产出 reasoning_content, 更慢更贵,
    # 且让模型有机会在正文里写出非 JSON 的铺垫). 这里显式关闭, 对应"最低思考深度".
    # 注意: OpenAI 那套 reasoning_effort=low|medium|high 是 M3.1-Flash-Preview 等模型的能力,
    # MiniMax-M3 的 Chat Completions 不认这个字段, 传了也不会降档, 别照搬.
    payload = {
        "model": config.MINIMAX_MODEL,
        "messages": build_messages(raw_text),
        "temperature": 0.2,
        "thinking": {"type": "disabled"},
    }

    headers = {
        "Authorization": f"Bearer {config.MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }

    # ====== 网络/超时: 1 次重试 (+2s 退避) ======
    last_err_code: str | None = None
    last_err_msg: str | None = None
    resp = None
    for attempt in range(2):  # 0=initial, 1=retry
        try:
            resp = requests.post(
                _endpoint_url(),
                headers=headers,
                json=payload,
                timeout=config.MINIMAX_TIMEOUT,
            )
            break  # 拿到 HTTP 响应 (无论状态码) 都退出重试循环
        except requests.exceptions.Timeout:
            last_err_code = "AI_TIMEOUT"
            last_err_msg = f"AI 解析超时 ({config.MINIMAX_TIMEOUT}s)"
        except requests.exceptions.RequestException as e:
            last_err_code = "AI_NETWORK"
            last_err_msg = f"AI 请求失败: {type(e).__name__}"

        if attempt == 0:
            logger.warning(
                "AI call attempt %d failed (%s): %s, retrying in 2s",
                attempt + 1, last_err_code, last_err_msg,
            )
            _time.sleep(2)

    if resp is None:
        # 重试用尽
        assert last_err_code is not None
        return _err(last_err_code, last_err_msg or "AI 调用失败")

    # ====== HTTP 状态码 ======
    if resp.status_code != 200:
        # 绝不打印响应体里的 Key (一般不会有, 但防御性处理)
        snippet = _redact(resp.text[:300]) if resp.text else ""
        return _err("AI_FORMAT", f"AI 返回 HTTP {resp.status_code}: {snippet}")

    # ====== JSON 解码 ======
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return _err("AI_FORMAT", "AI 返回非 JSON")

    # ====== OpenAI 兼容格式: data["choices"][0]["message"]["content"] ======
    try:
        content = data["choices"][0]["message"]["content"]
        model = data.get("model", config.MINIMAX_MODEL)
    except (KeyError, IndexError, TypeError) as e:
        return _err("AI_FORMAT", f"AI 返回结构异常: {type(e).__name__}")

    if not content or not content.strip():
        return _err("AI_FORMAT", "AI 返回内容为空")

    return {"content": content, "model": model}


def _err(code: str, message: str) -> dict:
    """构造结构化错误响应 (统一出口)."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "items": [],
        },
    }


# ====== JSON 健壮解析 ======

# 匹配 ```json ... ``` 或 ``` ... ``` 围栏
_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)

# 匹配首个 [ ... ] 或 { ... } JSON 块
_BRACKET_RE = re.compile(r"(\[[\s\S]*\]|\{[\s\S]*\})", re.DOTALL)


def _strip_fences(text: str) -> str:
    """去掉 ```json ... ``` 围栏 (如果存在)."""
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _extract_json(text: str) -> Any:
    """从文本里提取首个 JSON 数组或对象.

    顺序:
    1. 去掉围栏
    2. 尝试直接 parse
    3. 失败则用正则找首个 [..] 或 {..}
    """
    text = _strip_fences(text)

    # 1. 直接 parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 正则抓
    m = _BRACKET_RE.search(text)
    if not m:
        raise AIFormatError("AI 返回中找不到 JSON 结构")

    candidate = m.group(1)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise AIFormatError(f"AI 返回 JSON 解析失败: {e.msg}") from e


def _clean_baseline(raw: dict) -> dict[str, int]:
    """把家长配的基线洗成 {str: int}, 只保留 1~180 的合法值.

    设置页的键值表允许自由增删键 (前端不做枚举校验), 所以这里必须自己兜住:
    家长存了 {"订正": "abc"} 或 {"订正": 999} 时, 静默丢弃该项而不是让解析崩掉.
    """
    out: dict[str, int] = {}
    for k, v in (raw or {}).items():
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= iv <= 180:
            out[str(k).strip()] = iv
    return out


def _resolve_est_minutes(
    task_type: str | None,
    subject: str | None,
    ai_value: Any,
    baselines: tuple[dict[str, int], dict[str, int]] | None = None,
) -> int | None:
    """定 est_minutes: 家长配置 > AI 估值 > 科目基线.

    优先级 (家长在设置页配的数是硬约束, 见 01-功能设计 §7.2):
    1. task_type 命中 task_type_baseline_minutes -> 直接用配置值
    2. 否则用 AI 给的 est_minutes (仅当落在 1~180)
    3. 否则按 subject 查 subject_baseline_minutes
    4. 都没有 -> None, 由家长在预览表里填

    baselines 由调用方传入以便一次解析只读一次 DB; 省略时内部自取 (单测可直接调).
    """
    if baselines is None:
        baselines = _load_baselines()
    type_baseline, subject_baseline = baselines

    # 1. 任务类型基线 (强制覆盖 AI 估值)
    if task_type and task_type in type_baseline:
        return type_baseline[task_type]

    # 2. AI 估值 (越界/非法一律视为没有)
    if ai_value is not None and ai_value != "":
        try:
            ai_int = int(ai_value)
        except (TypeError, ValueError):
            ai_int = None
        if ai_int is not None and 1 <= ai_int <= 180:
            return ai_int

    # 3. 科目基线
    if subject and subject in subject_baseline:
        return subject_baseline[subject]

    return None


def _load_baselines() -> tuple[dict[str, int], dict[str, int]]:
    """读设置页的两份基线. 延迟导入避免 ai_client <-> services 循环依赖."""
    from .services import settings_service

    return (
        _clean_baseline(settings_service.get_task_type_baseline()),
        _clean_baseline(settings_service.get_subject_baseline()),
    )


def _coerce_item(raw: dict, baselines: tuple[dict[str, int], dict[str, int]] | None = None) -> dict:
    """把单条 AI 输出尽量归一化到 schema. 无法归一化的字段置 None / 默认值.

    - subject: 必须在 SUBJECTS, 否则置 '英语' (兜底, 家长确认页必改)
    - task_name: 必填, 缺失或空 -> 抛错
    - due_date: 字符串 -> date; 多种格式尽力解析; 失败置 None
    - deadline_time: 字符串 -> datetime; 失败置 None
    - is_optional: bool, 兜底 False
    - task_type: 归一到 TASK_TYPES; 缺失/other 时用关键词兜底
    - est_minutes: 家长配的任务类型基线 > AI 估值 > 科目基线
    - source_span: 必填, 缺失用 task_name
    - confidence: 必须在三档, 否则 'medium'

    baselines 形参用于一次解析内复用同一份基线 (避免每条都读一次 DB).
    """
    out: dict[str, Any] = {}

    # subject
    subj = str(raw.get("subject", "")).strip()
    if subj in config.SUBJECTS:
        out["subject"] = subj
    else:
        # 兜底: 看 task_name 里有没有科目关键词
        name_hint = str(raw.get("task_name", ""))
        if "语文" in name_hint:
            out["subject"] = "语文"
        elif "数学" in name_hint:
            out["subject"] = "数学"
        elif "英语" in name_hint:
            out["subject"] = "英语"
        else:
            out["subject"] = "英语"  # 家长确认页必改

    # task_name (必填)
    name = str(raw.get("task_name", "")).strip()
    if not name:
        # 用 source_span 兜底
        span = str(raw.get("source_span", "")).strip()
        if not span:
            raise ValueError("task_name 与 source_span 均缺失")
        name = span[:80]
    out["task_name"] = name[:200]

    # due_date
    out["due_date"] = _parse_date_loose(raw.get("due_date"))

    # deadline_time
    out["deadline_time"] = _parse_datetime_loose(raw.get("deadline_time"))

    # is_optional
    opt = raw.get("is_optional", False)
    if isinstance(opt, bool):
        out["is_optional"] = opt
    elif isinstance(opt, (int, float)):
        out["is_optional"] = bool(opt)
    elif isinstance(opt, str):
        out["is_optional"] = opt.strip().lower() in ("true", "1", "yes", "是", "选做")
    else:
        out["is_optional"] = False

    # task_type
    # 必须先于 est_minutes 归一化 —— 预估时长要按 task_type 查家长配的基线,
    # 顺序反了就会退化成"AI 自由发挥 + 前端兜底 15".
    # 缺失/other 时用关键词兜底 (功能设计 §7.2 规划项, 此前一直没实现).
    tt = str(raw.get("task_type") or "").strip()
    if tt not in config.TASK_TYPES or tt == "other":
        # 只扫 task_name, 绝不扫 source_span ——
        # source_span 常含同一条通知里**别的作业**的原文 (场景 E 要求 AI 拆开,
        # 但它经常整句照抄). 拿它做匹配会把邻居的类型安到本条头上:
        # 实测 "完成知能P15-16（部分课上已完成），订正前面作业" 会因为邻居的
        # "订正" 被判成 订正, 进而用 25 覆盖掉场景 C 下调后的 18 分钟.
        for kw, label in config.TASK_TYPE_KEYWORDS:
            if kw in name:
                tt = label
                break
        else:
            # 兜底不中: 保留 AI 原值 (可能是合法枚举或 other), 不强行改写
            tt = tt if tt in config.TASK_TYPES else None
    out["task_type"] = tt[:50] if tt else None

    # est_minutes
    # 家长在设置页配的基线是硬约束 (强制覆盖), AI 的估值只在没配基线时才用:
    #   1. task_type 命中 task_type_baseline_minutes -> 用配置值, 忽略 AI 估值
    #   2. 否则用 AI 给的合法值 (1~180)
    #   3. 还没有 -> 按 subject 查 subject_baseline_minutes
    #   4. 仍没有 -> None, 交家长在预览表里填
    out["est_minutes"] = _resolve_est_minutes(out.get("task_type"), out.get("subject"), raw.get("est_minutes"), baselines)

    # source_span (必填, 兜底用 task_name)
    span = str(raw.get("source_span", "")).strip()
    out["source_span"] = (span or name)[:500]

    # confidence
    conf = str(raw.get("confidence", "")).strip().lower()
    if conf in config.CONFIDENCE_LEVELS:
        out["confidence"] = conf
    else:
        out["confidence"] = "medium"

    return out


def _parse_date_loose(value: Any) -> str | None:
    """多种格式 -> 'YYYY-MM-DD'. 失败 -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in ("null", "none", "无"):
            return None
        # 常见: 2026-09-21 / 2026/9/21 / 9.21 / 9-21
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        # M.D 或 M-D 缺年
        m2 = re.match(r"^(\d{1,2})[\.\-/](\d{1,2})$", s)
        if m2:
            month, day = int(m2.group(1)), int(m2.group(2))
            year = datetime.now(timezone.utc).year
            try:
                return datetime(year, month, day).date().isoformat()
            except ValueError:
                return None
    return None


def _parse_datetime_loose(value: Any) -> str | None:
    """ISO8601 -> 'YYYY-MM-DDTHH:MM:SS'. 失败 -> None."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or s.lower() in ("null", "none", "无"):
        return None
    # 兼容带 Z / 无秒 / 空格分隔
    s2 = s.replace("Z", "+00:00").replace(" ", "T")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(s2, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def parse_ai_response(content: str) -> list[dict]:
    """从 AI 返回的 content 字符串解析出 items 列表.

    Raises:
        AIFormatError: 无法解析
    Returns:
        list of normalized dict (字段齐整)
    """
    parsed = _extract_json(content)

    # 兼容单个对象
    if isinstance(parsed, dict):
        # 可能是 {"items": [...]} 包装
        if "items" in parsed and isinstance(parsed["items"], list):
            parsed = parsed["items"]
        else:
            parsed = [parsed]

    if not isinstance(parsed, list):
        raise AIFormatError(f"AI 返回既不是数组也不是对象: {type(parsed).__name__}")

    normalized: list[dict] = []
    # 基线只读一次, 循环内复用 (每条都读 DB 会在长通知上放大成 N 次查询)
    baselines = _load_baselines()
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        for piece in _split_conjoined_item(raw):
            try:
                normalized.append(_coerce_item(piece, baselines))
            except ValueError:
                # 单条无法归一化, 跳过 (让其他条进来)
                continue

    return normalized


# ====== "并" 拆分兜底 (2026-10-13 家长报障) ======
# 报障原句: "1.补全课堂笔记，并完成知能P20-21。" 被解析成 1 条, 应拆成 2 条。
# 场景 E 的 prompt 规则写了 (2026-09-29), 但 prompt 是**软约束** —— M3 仍会
# 把"并"当成同一件事的连续步骤合并。预览表里合并掉的作业家长根本看不出漏了哪项,
# 所以这里在客户端补一道**确定性**拆分: 只认右半句是不是一个独立作业名。
#
# 为什么不是"看到并/，就拆": 那会把同一交付物的必经步骤也拆开,
# 例如 "订正1号作文本，仔细修改后誊抄在2号作文本上" 应该只有 1 条。
# 判据落在**右半句能否独立成作业名**上, 与 prompt 场景 E 的判据一致。

# 连接词: 可选的逗号/分号 + 并, 或裸的逗号/分号。
# 刻意不含"、" —— "完成知能P10、11" 是同一条里的枚举, 拆了就成两半截。
_CONJ_RE = re.compile(r"(?:[，,；;]\s*)?并\s*|[，,；;]\s*")

# 右半句以下列动作词开头时, 视为一条独立作业。
# 刻意**不含**: 誊抄/修改 (同一交付物的必经步骤, 见 prompt 场景 E 反例)、
# 做/写/整理 (太泛, "做完…，做…" 会误拆)、和 (枚举)。
_SPLIT_START_VERBS = (
    "完成", "订正", "背诵", "背", "预习", "阅读", "练习", "练",
    "计算", "默写", "抄写", "听写", "朗读", "复习",
)

# 右半句最短字数: 单字动词 ("订正") 拆出来家长看不出是什么作业。
_MIN_SPLIT_PART = 3

# 左半句只是日期/时间时的例外: "10月13日，完成作业" 是**一条**作业的时间标注,
# 不是两项作业。task_name 里其实不该出现日期 (归属日走 due_date), 但 AI 偶尔会带进来,
# 不挡一下就会凭空多出一条 "10月13日"。
_DATE_ONLY_RE = re.compile(
    r"^[\d\s./年月日号星期周礼拜末今昨明前后]+$"
)


def _is_standalone_task(part: str) -> bool:
    """判断一个片段能否独立成一条作业名."""
    if len(part) < _MIN_SPLIT_PART:
        return False
    return part.startswith(_SPLIT_START_VERBS)


def _split_segments(name: str) -> list[tuple[str, str]]:
    """按连接词切成 [(片段, 片段前的连接词), ...], 丢掉空片段."""
    parts: list[tuple[str, str]] = []
    start = 0
    for m in _CONJ_RE.finditer(name):
        parts.append((name[start:m.start()].strip(), name[m.start():m.end()]))
        start = m.end()
    parts.append((name[start:].strip(), ""))
    return [(text, sep) for text, sep in parts if text]


def _split_conjoined_item(raw: dict) -> list[dict]:
    """把 AI 合并成一条的 "A，并B" 拆成两条.

    只在拆得开时拆 (右半句是独立作业名); 其余原样返回, 保持向后兼容。
    拆分后的每条只带自己的 task_name/source_span, est_minutes 与 task_type
    交回 _coerce_item 按各自的任务类型重新算 —— 合并时 AI 给的那个时长是
    两项之和, 平分或沿用都不准。
    """
    name = str(raw.get("task_name", "")).strip()
    if not name:
        return [raw]

    segments = _split_segments(name)
    if len(segments) < 2:
        return [raw]
    # 左半句只是日期时整条不拆 (见 _DATE_ONLY_RE 注释)
    if _DATE_ONLY_RE.match(segments[0][0]):
        return [raw]

    pieces = [segments[0][0]]
    for text, sep in segments[1:]:
        if _is_standalone_task(text):
            pieces.append(text)
        else:
            # 不是独立作业 -> 合回上一条 (保留原连接词, 家长看到的仍是自己熟悉的那句话)
            pieces[-1] = pieces[-1] + sep + text

    if len(pieces) < 2:
        return [raw]

    span = str(raw.get("source_span", "")).strip()
    out: list[dict] = []
    for piece in pieces:
        item = dict(raw)
        item["task_name"] = piece
        # 片段能在原文里原样找到就用它当 source_span, 找不到就保留整段原文 ——
        # 宁可多给点上下文, 也不要给一段对不上的话.
        item["source_span"] = piece if (span and piece in span) else (span or piece)
        # 时长/类型按各自分支重算: 合并值对拆分后的任何一条都不准
        item["est_minutes"] = None
        item["task_type"] = None
        out.append(item)
    return out