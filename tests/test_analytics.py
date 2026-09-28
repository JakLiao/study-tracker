"""周复盘 + 设置 + 导出测试."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import patch


# ====== Analytics ======

def _seed_done_record(subject, task_type, est, actual, due_date_str, is_optional=False, ttype_override=None):
    """直接 SQL 插一条已完成作业 (绕过 PATCH)."""
    import sqlite3
    from app import config
    conn = sqlite3.connect(config.DB_PATH)
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO assignments
           (subject, task_name, due_date, deadline_time, is_optional,
            est_minutes, actual_minutes, status, task_type, source_span,
            confidence, batch_id, created_at, updated_at)
           VALUES (?, ?, ?, NULL, ?, ?, ?, 'done', ?, '', 'high', NULL, ?, ?)""",
        (subject, f"test {task_type}", due_date_str,
         1 if is_optional else 0, est, actual, ttype_override or task_type, now, now),
    )
    conn.commit()
    conn.close()


def test_analytics_sample_too_small_rejected(app_client):
    # 只有 2 条完成记录
    _seed_done_record("语文", "背诵", 15, 20, "2026-09-21")
    _seed_done_record("数学", "计算", 10, 12, "2026-09-22")
    r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4011"


def test_analytics_aggregates_by_subject_and_type(app_client):
    # 6 条完成: 语文背诵 估 15 实 25 (低估), 数学订正 估 30 实 20 (高估)
    monday = date(2026, 9, 21)  # 周一
    for i in range(3):
        _seed_done_record("语文", "背诵", 15, 25, (monday + timedelta(days=i)).isoformat())
    for i in range(3):
        _seed_done_record("数学", "订正", 30, 20, (monday + timedelta(days=i)).isoformat())
    # mock router 里的 datetime.now() (router 决定 today)
    fake_today = datetime(2026, 9, 25, 10, 0, 0)
    with patch("app.routers.analytics.datetime") as mock_dt:
        mock_dt.now.return_value = fake_today
        r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 200
    data = r.json()
    assert data["sample_size"] == 6
    types = {f"{x['subject']}|{x['task_type']}": x for x in data["by_type"]}
    assert "语文|背诵" in types
    assert "数学|订正" in types
    assert types["语文|背诵"]["diff_minutes"] > 0  # 低估
    assert types["数学|订正"]["diff_minutes"] < 0  # 高估


def test_observation_excluded_from_analytics(app_client):
    monday = date(2026, 9, 21)
    for i in range(5):
        _seed_done_record("数学", "observation", 10, 30, (monday + timedelta(days=i)).isoformat())
    r = app_client.get("/api/analytics/weekly?week=current")
    # observation 不计入, 实际样本为 0
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4011"


def test_optional_excluded_from_analytics(app_client):
    monday = date(2026, 9, 21)
    for i in range(5):
        _seed_done_record("数学", "计算", 10, 30, (monday + timedelta(days=i)).isoformat(), is_optional=True)
    r = app_client.get("/api/analytics/weekly?week=current")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4011"


def test_analytics_last_week(app_client):
    # 上周 5 条 (today 设为 2026-09-25, last_mon = 2026-09-14)
    last_mon = date(2026, 9, 14)
    for i in range(5):
        _seed_done_record("语文", "阅读", 20, 22, (last_mon + timedelta(days=i)).isoformat())
    fake_today = datetime(2026, 9, 25, 10, 0, 0)
    with patch("app.routers.analytics.datetime") as mock_dt:
        mock_dt.now.return_value = fake_today
        r = app_client.get("/api/analytics/weekly?week=last")
    assert r.status_code == 200
    assert r.json()["week"] == "last"


def test_analytics_invalid_week(app_client):
    r = app_client.get("/api/analytics/weekly?week=banana")
    # Query pattern regex 失败 -> RequestValidationError -> 4003
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4003"


# ====== Settings ======

def test_settings_default_seeded(app_client):
    r = app_client.get("/api/settings")
    assert r.status_code == 200
    items = {it["key"]: it["value"] for it in r.json()["items"]}
    assert "grade" in items
    assert "subject_baseline_minutes" in items


def test_settings_update_baseline(app_client):
    new_val = json.dumps({"语文": 25, "数学": 20, "英语": 20})
    r = app_client.patch("/api/settings/task_type_baseline_minutes", json={"value": new_val})
    assert r.status_code == 200
    r2 = app_client.get("/api/settings")
    items = {it["key"]: it["value"] for it in r2.json()["items"]}
    assert items["task_type_baseline_minutes"] == new_val


def test_settings_invalid_key_rejected(app_client):
    r = app_client.patch("/api/settings/not-a-key", json={"value": "x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4012"


def test_settings_invalid_json_value_rejected(app_client):
    r = app_client.patch("/api/settings/subject_baseline_minutes", json={"value": "not json {"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4013"


def test_settings_invalid_time_format_rejected(app_client):
    r = app_client.patch("/api/settings/dinner_time", json={"value": "下午六点半"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4013"


# ====== Export ======

def test_export_returns_json(app_client):
    _seed_done_record("语文", "背诵", 15, 20, "2026-09-21")
    r = app_client.get("/api/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.content)
    assert body["schema_version"] == 1
    assert body["counts"]["assignments"] >= 1
    assert "exported_at" in body


def test_export_empty_db(app_client):
    r = app_client.get("/api/export")
    assert r.status_code == 200
    body = json.loads(r.content)
    assert body["counts"]["assignments"] == 0


# ====== Health ======

def test_health_response_shape(app_client):
    r = app_client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    for k in ("status", "ai_reachable", "ai_model", "db_ok"):
        assert k in data