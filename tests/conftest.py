"""pytest 配置: PostgreSQL 测试库 + 关闭真实 AI 调用.

隔离策略:
- 每个测试开始前 TRUNCATE 全部业务表 (RESTART IDENTITY CASCADE) 再 seed 默认设置。
- 所有测试共享同一个 study_tracker_test 库, 不额外建库/删库。

连接串从哪来:
- TEST_DATABASE_URL 显式给了就用它;
- 否则从 .env 的 DATABASE_URL 派生 (只换库名, 主机/用户/密码原样沿用)。
- 两者都没有 -> 报错退出。密码只在 .env 里, 代码与文档都不留明文。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

# 让 `from app.xxx import yyy` 能找到
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import app.* 之前确定连接串 (config 在 import 时读取环境变量)
# .env 由 app.env_loader 解析 —— 与 config 走同一个实现, 不会各读各的
from app.env_loader import database_url_with_name, load_dotenv_if_exists  # noqa: E402

load_dotenv_if_exists(ROOT)

_BASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
TEST_DATABASE_URL = (os.environ.get("TEST_DATABASE_URL") or "").strip()
if not TEST_DATABASE_URL:
    if not _BASE_URL:
        raise RuntimeError(
            "未配置 DATABASE_URL: 复制 .env.example 为 .env 并填入数据库连接串"
            "(测试库连接串由它派生, 密码只写在 .env)"
        )
    TEST_DATABASE_URL = database_url_with_name(_BASE_URL, "study_tracker_test")

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["MINIMAX_API_KEY"] = ""  # 测试默认不调 AI

BUSINESS_TABLES = (
    "families",
    "children",
    "projects",
    "assignments",
    "parsed_batches",
    "completion_logs",
    "daily_reflections",
    "training_logs",
    "schedule_slots",
    "plan_items",
    "settings",
)


def _admin_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


def _db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


@pytest.fixture(scope="session", autouse=True)
def ensure_test_database():
    """确保测试库存在并完成迁移 (只跑一次)."""
    import psycopg

    name = _db_name(TEST_DATABASE_URL)
    with psycopg.connect(_admin_url(TEST_DATABASE_URL), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')

    from app.db import init_db

    init_db(TEST_DATABASE_URL)
    yield


@pytest.fixture(autouse=True)
def isolated_db(ensure_test_database):
    """每个测试前清空业务表并写入默认设置."""
    import app.config as cfg_mod
    from app.db import connect
    from app.seed import seed_settings

    # 有些用例会临时注入假 Key 并 reload config, 这里统一复位,
    # 避免"AI 是否可用"的断言受上一个用例的残留影响。
    cfg_mod.MINIMAX_API_KEY = ""
    with connect() as conn:
        conn.execute(
            "TRUNCATE " + ", ".join(BUSINESS_TABLES) + " RESTART IDENTITY CASCADE"
        )
    seed_settings()
    yield TEST_DATABASE_URL


@pytest.fixture
def app_client():
    """FastAPI TestClient, 连测试库."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())
