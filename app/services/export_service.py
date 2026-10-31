"""数据导出 (JSON 全量快照 + 本周作业清单 CSV)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from ..db import get_conn
from ..errors import ExportError


def export_all() -> bytes:
    family_id = default_family_id()
    try:
        with get_conn() as conn:
            assignments = [dict(r) for r in conn.execute(
                "SELECT * FROM assignments WHERE family_id = %s ORDER BY due_date, id",
                (family_id,),
            ).fetchall()]
            # M1: 项目是作业的归属来源, 不导出的话再导入会丢掉"这条是钢琴还是篮球"。
            projects = [dict(r) for r in conn.execute(
                "SELECT * FROM projects WHERE family_id = %s ORDER BY id",
                (family_id,),
            ).fetchall()]
            batches = [dict(r) for r in conn.execute(
                "SELECT * FROM parsed_batches ORDER BY id"
            ).fetchall()]
            logs = [dict(r) for r in conn.execute(
                "SELECT * FROM completion_logs WHERE assignment_id IN "
                "(SELECT id FROM assignments WHERE family_id = %s) ORDER BY at",
                (family_id,),
            ).fetchall()]
            settings = [dict(r) for r in conn.execute(
                "SELECT * FROM settings ORDER BY key"
            ).fetchall()]
        payload = {
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "assignments": len(assignments),
                "projects": len(projects),
                "batches": len(batches),
                "logs": len(logs),
                "settings": len(settings),
            },
            "assignments": assignments,
            "projects": projects,
            "parsed_batches": batches,
            "completion_logs": logs,
            "settings": settings,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    except Exception as e:
        raise ExportError(f"导出失败: {e}") from e