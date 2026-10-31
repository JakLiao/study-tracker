"""迁移定义 (PostgreSQL, 按 version 有序).

约定:
- 每个步骤都幂等 (IF NOT EXISTS / 先查存在再建), schema_meta 丢失重跑也不会毁数据。
- 不要在这里自己 BEGIN/COMMIT: 迁移器按 version 包事务, 失败整版回滚。
- PostgreSQL 的 DDL 是事务性的, 因此不需要像 SQLite 那样做"备份 + 文件回滚"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import psycopg

Step = str | Callable[[psycopg.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    steps: tuple[Step, ...]


# ====== v1: 家庭 / 孩子 / 项目 + 统一任务表 ======
#
# 三条业务约束落到 schema 上:
#   family_id / child_id 必填 -> 任何任务都能追溯到唯一归属, 天然隔离租户
#   subject 可空             -> 培训/习惯任务没有学科
#   task_kind               -> 学科作业 / 训练打卡 / 习惯, 统一任务池的区分依据
V1_STEPS: tuple[Step, ...] = (
    """
    CREATE TABLE IF NOT EXISTS families (
        id          BIGSERIAL PRIMARY KEY,
        name        TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS children (
        id          BIGSERIAL PRIMARY KEY,
        family_id   BIGINT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        grade       TEXT,
        created_at  TIMESTAMPTZ NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_children_family ON children(family_id)",
    """
    CREATE TABLE IF NOT EXISTS projects (
        id              BIGSERIAL PRIMARY KEY,
        family_id       BIGINT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
        child_id        BIGINT REFERENCES children(id) ON DELETE SET NULL,
        name            TEXT NOT NULL,
        project_type    TEXT NOT NULL DEFAULT 'custom',
        coach_name      TEXT,
        schedule_note   TEXT,
        status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','paused','archived')),
        created_at      TIMESTAMPTZ NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_projects_family ON projects(family_id)",
    "CREATE INDEX IF NOT EXISTS idx_projects_child ON projects(child_id)",
    """
    CREATE TABLE IF NOT EXISTS parsed_batches (
        id                  BIGSERIAL PRIMARY KEY,
        raw_text            TEXT NOT NULL,
        raw_text_hash       TEXT NOT NULL UNIQUE,
        parsed_at           TIMESTAMPTZ NOT NULL,
        ai_model_version    TEXT NOT NULL,
        result_count        INTEGER NOT NULL,
        status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','confirmed','discarded')),
        note                TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_batches_hash ON parsed_batches(raw_text_hash)",
    """
    CREATE TABLE IF NOT EXISTS assignments (
        id              BIGSERIAL PRIMARY KEY,
        family_id       BIGINT NOT NULL REFERENCES families(id),
        child_id        BIGINT NOT NULL REFERENCES children(id),
        project_id      BIGINT REFERENCES projects(id) ON DELETE SET NULL,
        subject         TEXT,
        task_name       TEXT NOT NULL,
        due_date        DATE NOT NULL,
        deadline_time   TIMESTAMP,
        is_optional     BOOLEAN NOT NULL DEFAULT FALSE,
        est_minutes     INTEGER NOT NULL CHECK (est_minutes BETWEEN 1 AND 180),
        actual_minutes  INTEGER CHECK (actual_minutes IS NULL OR actual_minutes BETWEEN 0 AND 240),
        status          TEXT NOT NULL DEFAULT 'todo'
                        CHECK (status IN ('todo','doing','done','skipped')),
        task_type       TEXT,
        task_kind       TEXT NOT NULL DEFAULT 'subject'
                        CHECK (task_kind IN ('subject','training','habit')),
        source_span     TEXT NOT NULL,
        confidence      TEXT NOT NULL CHECK (confidence IN ('high','medium','low')),
        batch_id        BIGINT REFERENCES parsed_batches(id) ON DELETE SET NULL,
        created_at      TIMESTAMPTZ NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_assignments_due_date ON assignments(due_date)",
    "CREATE INDEX IF NOT EXISTS idx_assignments_subject_due ON assignments(subject, due_date)",
    "CREATE INDEX IF NOT EXISTS idx_assignments_status ON assignments(status)",
    "CREATE INDEX IF NOT EXISTS idx_assignments_family_due ON assignments(family_id, due_date)",
    "CREATE INDEX IF NOT EXISTS idx_assignments_child_due ON assignments(child_id, due_date)",
    """
    CREATE TABLE IF NOT EXISTS completion_logs (
        id              BIGSERIAL PRIMARY KEY,
        assignment_id   BIGINT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
        action          TEXT NOT NULL
                        CHECK (action IN ('created','status_changed','actual_logged','edited','deleted')),
        from_value      TEXT,
        to_value        TEXT,
        at              TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_logs_assignment_at ON completion_logs(assignment_id, at)",
    """
    CREATE TABLE IF NOT EXISTS settings (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL
    )
    """,
)


# ====== v2: 每日三问 (M3 日结) ======
#
# F8 晚间三问: 难度 / 专注 / 情绪各一个固定档位, 30 秒记完。
# 为什么单独建表而不是塞进 settings:
#   - settings 是"家庭配置"的单值键, 一天一行的状态塞进去会变成
#     "reflect_2026-10-30" 这种键名, 周报要按日期区间扫键名, 既慢又脏;
#   - 这是孩子的纵向数据(将来要按周/学期聚合), 必须能按日期建索引。
#
# focus 可空: 1–2 年级用两问表情版(难度+情绪), 专注一题留空 —— 低龄孩子
# 对"专注"的自我报告不可靠, 不如不问。mood 必填, 因为它是当天最直接的信号。
# UNIQUE(family_id, child_id, reflect_date): 一天一条, 重复提交按覆盖处理
# (家长晚上记错了要改), 用 ON CONFLICT 实现幂等 upsert。
V2_STEPS: tuple[Step, ...] = (
    """
    CREATE TABLE IF NOT EXISTS daily_reflections (
        id              BIGSERIAL PRIMARY KEY,
        family_id       BIGINT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
        child_id        BIGINT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
        reflect_date    DATE NOT NULL,
        difficulty      TEXT NOT NULL CHECK (difficulty IN ('easy','ok','hard')),
        focus           TEXT CHECK (focus IN ('good','ok','bad')),
        mood            TEXT NOT NULL CHECK (mood IN ('good','ok','bad')),
        note            TEXT,
        created_at      TIMESTAMPTZ NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL,
        UNIQUE (family_id, child_id, reflect_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reflections_family_date "
    "ON daily_reflections(family_id, reflect_date)",
)


# ====== v3: 训练打卡 (M4) ======
#
# F18: 练琴/体育的"30 秒打卡" —— 时长 + 组数/次数 + 曲目段落。
#
# 为什么绑定 assignment 而不是只绑 project:
#   打卡记录的是"今天这条训练任务做了没有、做了多少", 而"今天有哪些
#   训练任务"本来就是 assignments 的一行。绑项目只能知道"钢琴练过",
#   却对不上看板上那条"曲目练习"的完成状态, 两边要各自维护一套进度。
#
# UNIQUE(assignment_id): 一条任务对应一次打卡。同一天重复练 (比如上午
# 音阶、晚上曲目) 在任务池里本来就是两条 assignment, 一任务一记录才
# 不会把两次练习压成一条。重复提交按覆盖处理 (家长填错了要能改)。
#
# duration_minutes 允许 0: 有些训练是"今天上课, 回家不用练", 家长仍想
# 记一笔"完成"; 0 分钟与"没打卡"是两件事, 不能混为一谈。
# sets / reps 可空: 钢琴记段落不记组数, 篮球记组数不记段落, 一个表
# 同时容纳两类, 不必为每种项目单独建表。
V3_STEPS: tuple[Step, ...] = (
    """
    CREATE TABLE IF NOT EXISTS training_logs (
        id                BIGSERIAL PRIMARY KEY,
        family_id         BIGINT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
        child_id          BIGINT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
        assignment_id     BIGINT NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
        project_id        BIGINT REFERENCES projects(id) ON DELETE SET NULL,
        log_date          DATE NOT NULL,
        duration_minutes  INTEGER NOT NULL CHECK (duration_minutes BETWEEN 0 AND 240),
        sets              INTEGER CHECK (sets BETWEEN 0 AND 100),
        reps              INTEGER CHECK (reps BETWEEN 0 AND 1000),
        segment           TEXT,
        note              TEXT,
        created_at        TIMESTAMPTZ NOT NULL,
        updated_at        TIMESTAMPTZ NOT NULL,
        UNIQUE (assignment_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_training_family_date "
    "ON training_logs(family_id, log_date)",
    "CREATE INDEX IF NOT EXISTS idx_training_project_date "
    "ON training_logs(project_id, log_date)",
)


# ====== v4: 周课表 + 周计划项 (M5) ======
#
# F19 周课表: "钢琴周三 16:30-18:00"这类固定上课时段, 用来算每天的总负荷
# 和发现排期冲突。它是"计划", 不是"任务" —— 课表本身不落 assignments,
# 只有家长点「生成本周任务」时才把周计划项物化成任务。
#
# weekday 0=周一 ... 6=周日, 与 Python date.weekday() 对齐, 避免前后端
# 各有一套"周日是 0 还是 7"的换算。
# UNIQUE(family_id, project_id, weekday, start_time): 同一个项目同一时刻
# 只能有一节课; 重复点保存不会长出两条一样的课表。
# CHECK (end_time > start_time): 跨午夜的课 (如 23:30-00:30) 不支持,
# 小学生培训不会排到这个点, 允许反而会让"时长"算成负数。
#
# plan_items 是"每周重复做什么"的模板: 星期几 + 任务名 + 预计时长。
# weekdays 用逗号分隔的 0-6 ("0,2,4" = 一三五), 而不是 7 个布尔列:
# 一张表最多 7 天, 布尔列会让每加一个字段就多 7 列; 逗号串在服务层
# 解析成数组, 校验和展示都简单。为什么不用 PG 数组: 导出 JSON 时
# 数组是原生类型, 但本项目所有时间/日期字段都以文本导出保持一致,
# 这里也保持文本, 避免导出格式出现两种数组表达。
#
# plan_items 不存"哪一周": 它就是每周默认节奏, 生成时按当前周展开。
# 计划文档里的"默认继承上周 / 只改一天"在 MVP 用"改这一天的任务"
# 实现 (生成后直接在看板改), 不引入按周快照的复杂度。
V4_STEPS: tuple[Step, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schedule_slots (
        id          BIGSERIAL PRIMARY KEY,
        family_id   BIGINT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
        child_id    BIGINT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
        project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        weekday     SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
        start_time  TIME NOT NULL,
        end_time    TIME NOT NULL,
        note        TEXT,
        created_at  TIMESTAMPTZ NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL,
        UNIQUE (family_id, project_id, weekday, start_time),
        CHECK (end_time > start_time)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_slots_family_weekday "
    "ON schedule_slots(family_id, weekday, start_time)",
    """
    CREATE TABLE IF NOT EXISTS plan_items (
        id          BIGSERIAL PRIMARY KEY,
        family_id   BIGINT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
        child_id    BIGINT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
        task_kind   TEXT NOT NULL DEFAULT 'subject'
                    CHECK (task_kind IN ('subject','training','habit')),
        subject     TEXT,
        project_id  BIGINT REFERENCES projects(id) ON DELETE SET NULL,
        task_name   TEXT NOT NULL,
        est_minutes INTEGER NOT NULL CHECK (est_minutes BETWEEN 1 AND 180),
        weekdays    TEXT NOT NULL,
        active      BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMPTZ NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_plan_items_family "
    "ON plan_items(family_id, active)",
)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, name="baseline", steps=V1_STEPS),
    Migration(version=2, name="daily_reflections", steps=V2_STEPS),
    Migration(version=3, name="training_logs", steps=V3_STEPS),
    Migration(version=4, name="week_plan", steps=V4_STEPS),
)
