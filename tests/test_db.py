"""DB 初始化 + 默认 seed 测试 (PostgreSQL)."""
from __future__ import annotations

import psycopg
from psycopg.rows import tuple_row


def test_init_creates_tables(isolated_db):
    with psycopg.connect(isolated_db, row_factory=tuple_row) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchall()
    names = {r[0] for r in rows}
    for t in ("assignments", "parsed_batches", "completion_logs", "settings"):
        assert t in names, f"缺表: {t}"


def test_init_creates_indexes(isolated_db):
    with psycopg.connect(isolated_db, row_factory=tuple_row) as conn:
        rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        ).fetchall()
    idx_names = {r[0] for r in rows}
    assert "idx_assignments_due_date" in idx_names
    assert "idx_assignments_subject_due" in idx_names
    assert "idx_assignments_status" in idx_names
    assert "idx_batches_hash" in idx_names
    assert "idx_logs_assignment_at" in idx_names


def test_seed_writes_defaults(isolated_db):
    with psycopg.connect(isolated_db, row_factory=tuple_row) as conn:
        rows = conn.execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    keys = {r[0]: r[1] for r in rows}
    for k in (
        "grade",
        "subject_baseline_minutes",
        "task_type_baseline_minutes",
        "school_dismiss_time",
        "dinner_time",
        "ai_model_version",
    ):
        assert k in keys, f"缺默认设置: {k}"


def test_seed_is_idempotent(isolated_db):
    # 跑两次 seed 不应报错
    from app.seed import seed_settings

    seed_settings()
    seed_settings()
    with psycopg.connect(isolated_db, row_factory=tuple_row) as conn:
        count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    assert count == 6
