"""M0 数据底座 (PostgreSQL): 建表 / 幂等 / dry-run / 租户隔离."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.rows import dict_row

from app import config


def _admin_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


@pytest.fixture
def scratch_db():
    """独立临时库: 迁移测试不能碰共享测试库的 schema_meta."""
    name = f"study_scratch_{uuid.uuid4().hex[:10]}"
    admin = _admin_url(config.DATABASE_URL)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    parts = urlsplit(config.DATABASE_URL)
    url = urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))
    try:
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _table_exists(url: str, table: str) -> bool:
    with psycopg.connect(url, row_factory=dict_row) as conn:
        return bool(
            conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS exists", (table,)
            ).fetchone()["exists"]
        )


def test_migration_creates_schema_and_is_idempotent(scratch_db):
    from app.migrations import migrate

    first = migrate(scratch_db)
    # 0002 = M3 每日三问, 0003 = M4 训练打卡, 0004 = M5 周课表与周计划。
    expected = [
        "0001_baseline",
        "0002_daily_reflections",
        "0003_training_logs",
        "0004_week_plan",
    ]
    assert first["pending"] == expected
    assert first["applied"] == expected
    assert first["version_after"] == 4
    assert first["assignments_before"] is None
    assert first["assignments_after"] == 0
    assert _table_exists(scratch_db, "families")
    assert _table_exists(scratch_db, "assignments")
    assert _table_exists(scratch_db, "daily_reflections")
    assert _table_exists(scratch_db, "training_logs")
    assert _table_exists(scratch_db, "schedule_slots")
    assert _table_exists(scratch_db, "plan_items")

    second = migrate(scratch_db)
    assert second["pending"] == []
    assert second["applied"] == []
    assert second["version_after"] == 4


def test_dry_run_does_not_create_schema(scratch_db):
    from app.migrations import migrate, pending_migrations

    expected = [
        "0001_baseline",
        "0002_daily_reflections",
        "0003_training_logs",
        "0004_week_plan",
    ]
    assert pending_migrations(scratch_db) == expected
    report = migrate(scratch_db, dry_run=True)
    assert report["dry_run"] is True
    assert report["applied"] == expected

    # 回滚后真实库里不应留下任何表
    assert not _table_exists(scratch_db, "families")
    assert not _table_exists(scratch_db, "schema_meta")


def test_tenant_columns_and_nullable_subject(scratch_db):
    """培训任务可以没有学科; family_id / child_id 必填."""
    from app.migrations import migrate
    from app.services.tenant import ensure_default_family_child

    migrate(scratch_db)
    with psycopg.connect(scratch_db, row_factory=dict_row) as conn:
        family_id, child_id = ensure_default_family_child(conn=conn)
        now = datetime.now(timezone.utc)
        conn.execute(
            """
            INSERT INTO assignments
                (subject, task_name, due_date, est_minutes, status, source_span,
                 confidence, created_at, updated_at, family_id, child_id, task_kind)
            VALUES (NULL, '钢琴音阶 15 分钟', '2026-09-23', 15, 'todo', '教练群',
                    'high', %s, %s, %s, %s, 'training')
            """,
            (now, now, family_id, child_id),
        )
        conn.execute("COMMIT")
        count = conn.execute("SELECT COUNT(*) AS n FROM assignments").fetchone()["n"]
        assert count == 1

        # 默认家庭/孩子只建一套 (幂等)
        f2, c2 = ensure_default_family_child(conn=conn)
        assert (f2, c2) == (family_id, child_id)
        assert conn.execute("SELECT COUNT(*) AS n FROM families").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM children").fetchone()["n"] == 1


def test_api_assignments_are_tenant_scoped(app_client):
    """另一家庭的作业不能出现在列表 / 看板 / 日历里."""
    date_str = "2026-10-30"
    r = app_client.post(
        "/api/assignments/batch",
        json={
            "items": [{
                "subject": "语文",
                "task_name": "家庭 A 的作业",
                "due_date": date_str,
                "est_minutes": 15,
            }]
        },
    )
    assert r.status_code == 200, r.text

    # 直接插一个"别人家"的家庭与作业
    now = datetime.now(timezone.utc)
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        family_id = conn.execute(
            "INSERT INTO families (name, created_at, updated_at) VALUES ('另一个家庭', %s, %s) RETURNING id",
            (now, now),
        ).fetchone()["id"]
        child_id = conn.execute(
            "INSERT INTO children (family_id, name, grade, created_at, updated_at) "
            "VALUES (%s, '别人家孩子', '3', %s, %s) RETURNING id",
            (family_id, now, now),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO assignments
                (subject, task_name, due_date, est_minutes, status, source_span,
                 confidence, created_at, updated_at, family_id, child_id, task_kind)
            VALUES ('数学', '别人家的作业', %s, 10, 'todo', '', 'high', %s, %s, %s, %s, 'subject')
            """,
            (date_str, now, now, family_id, child_id),
        )
        conn.execute("COMMIT")

    listed = app_client.get("/api/assignments", params={"date": date_str}).json()["items"]
    names = [i["task_name"] for i in listed]
    assert names == ["家庭 A 的作业"]

    board = app_client.get("/api/board", params={"date": date_str}).json()
    assert board["progress"]["total"] == 1
    assert board["progress"]["required"] == 1

    calendar = app_client.get(
        "/api/assignments/calendar", params={"year": 2026, "month": 10}
    ).json()
    assert calendar["days"][date_str]["total"] == 1
