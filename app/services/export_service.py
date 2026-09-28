"""数据导出 (JSON)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..db import get_conn
from ..errors import ExportError


def export_all() -> bytes:
    try:
        with get_conn() as conn:
            assignments = [dict(r) for r in conn.execute(
                "SELECT * FROM assignments ORDER BY due_date, id"
            ).fetchall()]
            batches = [dict(r) for r in conn.execute(
                "SELECT * FROM parsed_batches ORDER BY id"
            ).fetchall()]
            logs = [dict(r) for r in conn.execute(
                "SELECT * FROM completion_logs ORDER BY at"
            ).fetchall()]
            settings = [dict(r) for r in conn.execute(
                "SELECT * FROM settings ORDER BY key"
            ).fetchall()]
        payload = {
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "assignments": len(assignments),
                "batches": len(batches),
                "logs": len(logs),
                "settings": len(settings),
            },
            "assignments": assignments,
            "parsed_batches": batches,
            "completion_logs": logs,
            "settings": settings,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    except Exception as e:
        raise ExportError(f"导出失败: {e}") from e