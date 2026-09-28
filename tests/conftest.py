"""pytest 配置: 临时 DB + 关闭真实 AI 调用."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 让 `from app.xxx import yyy` 能找到
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """每个测试用独立临时 DB, 互不污染."""
    db_path = tmp_path / "test_study.db"
    monkeypatch.setenv("STUDY_DB_PATH", str(db_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "")  # 测试默认不调 AI
    # 必须 reload config, 因为它是模块级常量
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)
    import app.db as db_mod
    importlib.reload(db_mod)
    import app.seed as seed_mod
    importlib.reload(seed_mod)
    db_mod.init_db()
    db_mod.apply_wal_pragma()
    seed_mod.seed_settings()
    yield db_path
    # 清理
    if db_path.exists():
        db_path.unlink()
    for ext in ("-wal", "-shm", "-journal"):
        p = db_path.with_name(db_path.name + ext)
        if p.exists():
            p.unlink()


@pytest.fixture
def app_client():
    """FastAPI TestClient, 用 isolated_db 的 DB."""
    # reload 各模块确保 config/db 用的是 fixture 设的环境
    import importlib
    import app.main as main_mod
    importlib.reload(main_mod)
    import app.routers.analytics as a
    importlib.reload(a)
    import app.routers.assignments as asg
    importlib.reload(asg)
    import app.routers.board as b
    importlib.reload(b)
    import app.routers.export as e
    importlib.reload(e)
    import app.routers.health as h
    importlib.reload(h)
    import app.routers.parse as p
    importlib.reload(p)
    import app.routers.settings as s
    importlib.reload(s)
    importlib.reload(main_mod)
    from fastapi.testclient import TestClient
    return TestClient(main_mod.app)