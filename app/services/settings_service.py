"""设置项读写."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import config
from ..db import get_conn
from ..errors import BadSettingKeyError, BadSettingValueError


# 允许的设置 key
ALLOWED_KEYS = frozenset({
    "grade",
    "subject_baseline_minutes",
    "task_type_baseline_minutes",
    "school_dismiss_time",
    "dinner_time",
    "ai_model_version",
})


def list_settings() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value, updated_at FROM settings ORDER BY key").fetchall()
    return [{"key": r["key"], "value": r["value"], "updated_at": r["updated_at"]} for r in rows]


def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else None


def update_setting(key: str, raw_value: str) -> dict:
    if key not in ALLOWED_KEYS:
        raise BadSettingKeyError(f"未注册的 key: {key}")
    # 校验 value 格式 (按 key 区分)
    value = raw_value.strip()
    if not value:
        raise BadSettingValueError("value 不能为空")
    if key.endswith("_minutes"):
        try:
            data = json.loads(value)
            if not isinstance(data, dict):
                raise BadSettingValueError(f"{key} 必须是 JSON 对象")
        except json.JSONDecodeError as e:
            raise BadSettingValueError(f"{key} JSON 解析失败: {e.msg}") from None
        # 每项必须是 1~180 的整数 —— 这份配置直接决定解析出的 est_minutes,
        # 放进 {"订正": "abc"} 或 {"订正": 999} 会在入库时被 DB CHECK 挡下, 家长只看到一句报错.
        # 在这里就拒掉, 错误信息能指到具体是哪个键.
        for k, v in data.items():
            if not str(k).strip():
                raise BadSettingValueError(f"{key} 含空键")
            try:
                iv = int(v)
            except (TypeError, ValueError):
                raise BadSettingValueError(f"{key} 的 {k!r} 必须是整数分钟, 当前是 {v!r}") from None
            if not 1 <= iv <= 180:
                raise BadSettingValueError(f"{key} 的 {k!r} 必须在 1~180 分钟, 当前是 {iv}")
    if key in ("school_dismiss_time", "dinner_time"):
        # 形如 HH:MM
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as e:
            raise BadSettingValueError(f"{key} 必须为 HH:MM 格式") from None
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, %s) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, now),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"key": key, "value": value, "updated_at": now}


def get_subject_baseline() -> dict[str, int]:
    raw = get_setting("subject_baseline_minutes")
    if not raw:
        return dict(config.DEFAULT_SUBJECT_BASELINE)
    try:
        data = json.loads(raw)
        return {k: int(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, TypeError):
        return dict(config.DEFAULT_SUBJECT_BASELINE)


def get_task_type_baseline() -> dict[str, int]:
    raw = get_setting("task_type_baseline_minutes")
    if not raw:
        return dict(config.DEFAULT_TASK_TYPE_BASELINE)
    try:
        data = json.loads(raw)
        return {k: int(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, TypeError):
        return dict(config.DEFAULT_TASK_TYPE_BASELINE)