"""启动时种子: 写入默认设置 (若 key 不存在).

幂等: INSERT ... ON CONFLICT DO NOTHING.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config
from .db import get_conn


def seed_settings() -> None:
    now = datetime.now(timezone.utc)
    # ensure_ascii=False: 让中文字符以原样落库, 避免 \uXXXX 转义形式 (P1: 设置页面可读性 + DB 体积 + 导出友好)
    defaults = {
        "grade": json.dumps(config.DEFAULT_GRADE, ensure_ascii=False),
        "subject_baseline_minutes": json.dumps(config.DEFAULT_SUBJECT_BASELINE, ensure_ascii=False),
        "task_type_baseline_minutes": json.dumps(config.DEFAULT_TASK_TYPE_BASELINE, ensure_ascii=False),
        "school_dismiss_time": config.DEFAULT_SCHOOL_DISMISS,
        "dinner_time": config.DEFAULT_DINNER,
        "ai_model_version": config.MINIMAX_MODEL,
    }
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            for key, value in defaults.items():
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (key) DO NOTHING",
                    (key, value, now),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
