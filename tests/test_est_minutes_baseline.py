"""回归测试: 设置页的「各作业类型预估用时」必须真的作用到解析结果.

家长报障 (2026-09-29): 把「阅读」改成 10 分钟, 解析出来仍是 15/20 分钟, 与配置无关.

根因: task_type_baseline_minutes 是个"只写不读"的配置孤岛 ——
settings_service.get_task_type_baseline() 全仓没有调用方, est_minutes 100% 由 AI 自由发挥,
前端再用硬编码的 15 兜底. 功能设计 §7.2 承诺的"默认用 task_type_baseline_minutes"从未实现.

本文件锁住三段链路:
1. _coerce_item 层: 配置值强制覆盖 AI 估值
2. task_type 关键词兜底: AI 没给类型时也能命中配置
3. 端到端: PATCH 设置 -> POST /api/parse -> 断言 est_minutes 变成配置值
"""
from __future__ import annotations

import json

import pytest

from app.ai_client import _clean_baseline, _coerce_item, _resolve_est_minutes, parse_ai_response


# 显式传基线, 让这批用例不依赖 DB —— 测的是"覆盖规则"本身
TYPES = {"订正": 25, "背诵": 15, "预习": 20, "阅读": 10, "计算": 10, "抄写": 15}
SUBJECTS = {"语文": 20, "数学": 15, "英语": 15}
BL = (TYPES, SUBJECTS)


def _item(**kw):
    base = {"subject": "语文", "task_name": "任务", "source_span": "原文"}
    base.update(kw)
    return base


# ====== 1. 配置强制覆盖 AI 估值 (家长报障的直接场景) ======

def test_configured_type_baseline_overrides_ai_value():
    """家长配 阅读=10, AI 说 20 -> 必须是 10."""
    out = _coerce_item(
        _item(subject="语文", task_name="阅读课外书", task_type="阅读", est_minutes=20),
        BL,
    )
    assert out["task_type"] == "阅读"
    assert out["est_minutes"] == 10


def test_configured_baseline_beats_ai_for_every_type():
    """逐个类型验证: 配了多少就出多少, 与 AI 给的数无关."""
    for ttype, minutes in TYPES.items():
        out = _coerce_item(
            _item(task_name=f"{ttype}任务", task_type=ttype, est_minutes=99),
            BL,
        )
        assert out["est_minutes"] == minutes, f"{ttype} 应被配置覆盖为 {minutes}"


def test_ai_value_used_when_type_not_configured():
    """类型没配基线时保留 AI 估值 (不能把所有 AI 判断都抹掉)."""
    out = _coerce_item(
        _item(task_name="手工折纸", task_type="other", est_minutes=33),
        BL,
    )
    assert out["est_minutes"] == 33


def test_subject_baseline_used_when_no_type_and_no_ai_value():
    """既无类型基线也无 AI 估值 -> 退到科目基线, 而不是 None."""
    out = _coerce_item(_item(subject="数学", task_name="不明任务", est_minutes=None), BL)
    assert out["est_minutes"] == 15


def test_observation_keeps_ai_value():
    """通勤观察不在任务类型基线里, 必须保留 AI 给的时长, 不能被科目基线改写."""
    out = _coerce_item(
        _item(subject="语文", task_name="1 千米体验记录单 (通勤观察)",
              task_type="observation", est_minutes=5),
        BL,
    )
    assert out["est_minutes"] == 5


def test_invalid_ai_value_out_of_range_ignored():
    """AI 给 999 视为没有, 落到科目基线 (旧行为是 None, 现在不该再空着)."""
    out = _coerce_item(_item(subject="语文", task_name="不明", est_minutes=999), BL)
    assert out["est_minutes"] == 20


# ====== 2. task_type 关键词兜底 ======

def test_task_type_keyword_fallback_from_task_name():
    """AI 没给 task_type, 从任务名认出「阅读」-> 命中配置."""
    out = _coerce_item(_item(task_name="阅读课外书", est_minutes=20), BL)
    assert out["task_type"] == "阅读"
    assert out["est_minutes"] == 10


def test_task_type_never_inferred_from_source_span():
    """回归: 关键词兜底只能扫 task_name, 不能扫 source_span.

    source_span 常把同一条通知里别的作业原文也抄进来 (场景 E 本该拆开, 但 AI 经常整句照抄).
    拿它匹配会把邻居的类型安到本条头上 —— 实测害处:
    "完成知能P15-16（部分课上已完成），订正前面作业" 会因邻居的"订正"被判成 订正,
    再用订正基线 25 覆盖掉场景 C 下调后的 18 分钟.
    """
    out = _coerce_item(
        _item(task_name="完成知能 P15-16 (课上已部分完成)",
              source_span="完成知能P15-16（部分课上已完成），订正前面作业",
              est_minutes=18),
        BL,
    )
    assert out["task_type"] is None
    assert out["est_minutes"] == 18


def test_task_type_other_gets_keyword_fallback():
    """AI 明确给 other 时, 仍允许关键词兜底 —— 家长报障里 AI 常给 other."""
    out = _coerce_item(_item(task_name="背诵课文 P18-19", task_type="other", est_minutes=20), BL)
    assert out["task_type"] == "背诵"
    assert out["est_minutes"] == 15


def test_specific_keyword_wins_over_generic_reading():
    """"抄写阅读心得" 应判成 抄写而不是 阅读 (顺序不能反)."""
    out = _coerce_item(_item(task_name="抄写阅读心得", est_minutes=30), BL)
    assert out["task_type"] == "抄写"


def test_no_keyword_leaves_task_type_none():
    """认不出类型就留 None, 不硬塞一个错的类型."""
    out = _coerce_item(_item(subject="数学", task_name="折纸手工", est_minutes=None), BL)
    assert out["task_type"] is None
    assert out["est_minutes"] == 15  # 落到科目基线


# ====== 3. 配置清洗 ======

def test_clean_baseline_drops_invalid_values():
    """家长存了垃圾值不能把解析搞崩, 静默丢弃该项."""
    assert _clean_baseline({"阅读": 10, "背诵": "abc", "预习": 999, "计算": 0}) == {"阅读": 10}


def test_clean_baseline_handles_none():
    assert _clean_baseline(None) == {}


def test_resolve_without_baselines_returns_none_for_unknown():
    """类型/科目都没配且 AI 也没值 -> None, 交家长填."""
    assert _resolve_est_minutes(None, None, None, ({}, {})) is None


# ====== 4. 端到端: 改设置 -> 解析真的变 ======

@pytest.fixture
def parse_with_settings(app_client, monkeypatch):
    """可改设置再解析的 client. AI 返回固定的「阅读课外书」一条."""
    import importlib
    import app.config as cfg_mod
    cfg_mod.MINIMAX_API_KEY = "sk-fake"

    import app.routers.parse as parse_mod
    importlib.reload(parse_mod)

    ai_output = [{
        "subject": "语文", "task_name": "阅读课外书", "due_date": "2026-09-29",
        "deadline_time": None, "is_optional": False, "est_minutes": 20,
        "task_type": "阅读", "source_span": "阅读课外书", "confidence": "high",
    }]

    def fake_call(raw_text):
        return {"content": json.dumps(ai_output, ensure_ascii=False), "model": "test-mock"}

    monkeypatch.setattr(parse_mod, "call_minimax", fake_call)
    return app_client


def test_end_to_end_setting_change_affects_parse(parse_with_settings):
    """家长报障的完整复现: 把阅读改成 10 -> 解析结果必须是 10, 不是 AI 的 20."""
    r = parse_with_settings.patch(
        "/api/settings/task_type_baseline_minutes",
        json={"value": json.dumps({"阅读": 10}, ensure_ascii=False)},
    )
    assert r.status_code == 200

    data = parse_with_settings.post("/api/parse", json={"raw_text": "今天阅读课外书"}).json()
    assert data["ok"] is True
    assert len(data["items"]) == 1
    assert data["items"][0]["est_minutes"] == 10


def test_end_to_end_changing_setting_back_changes_result(parse_with_settings):
    """配置是真参数, 不是初始化时读一次的常量 —— 改回 30 应该立刻生效."""
    for minutes in (10, 30):
        parse_with_settings.patch(
            "/api/settings/task_type_baseline_minutes",
            json={"value": json.dumps({"阅读": minutes}, ensure_ascii=False)},
        )
        data = parse_with_settings.post("/api/parse", json={"raw_text": "今天阅读课外书"}).json()
        assert data["items"][0]["est_minutes"] == minutes


def test_settings_reject_out_of_range_minutes(parse_with_settings):
    """越界值必须在保存时被拒 —— 否则会一路飘到入库才炸."""
    r = parse_with_settings.patch(
        "/api/settings/task_type_baseline_minutes",
        json={"value": json.dumps({"阅读": 999}, ensure_ascii=False)},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "4013"


def test_settings_reject_non_numeric_minutes(parse_with_settings):
    r = parse_with_settings.patch(
        "/api/settings/task_type_baseline_minutes",
        json={"value": json.dumps({"阅读": "十分钟"}, ensure_ascii=False)},
    )
    assert r.status_code == 400


# ====== 5. parse_ai_response 走真实 DB 读取 ======

def test_parse_ai_response_reads_db_baseline(app_client):
    """parse_ai_response 自己会去 DB 取基线 (之前这里是断链的)."""
    from app.services import settings_service
    settings_service.update_setting(
        "task_type_baseline_minutes", json.dumps({"阅读": 7}, ensure_ascii=False)
    )
    content = json.dumps([{
        "subject": "语文", "task_name": "阅读课外书", "source_span": "阅读课外书",
        "task_type": "阅读", "est_minutes": 20,
    }], ensure_ascii=False)
    items = parse_ai_response(content)
    assert items[0]["est_minutes"] == 7
