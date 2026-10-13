"""回归测试: build_messages() 必须在含 JSON 花括号的模板上不抛异常.

P0 回归 (慧文验收反馈 2026-09-28):
- 之前版本用 .format() 处理含 JSON schema 的 prompt, KeyError 直接 500
- 改用 string.Template 后, 必须有这条测试守住, 防止有人改回 .format()

包含:
1. 真实调用 build_messages() (不 mock 任何东西)
3. 覆盖两种模板 (含 JSON schema / 含示例 JSON)
4. 用户原文含 $variable 不炸
5. 9.21 / 9.22 真实样本也走通
"""
from __future__ import annotations

import pytest

from app.prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    build_messages,
    example_user_payload,
)


def test_build_messages_does_not_raise():
    """P0: build_messages 不能因为模板里有字面量 JSON 花括号就炸."""
    msgs = build_messages("任意文本")
    assert isinstance(msgs, list)
    assert len(msgs) == 2


def test_build_messages_structure():
    """消息结构: [system, user], role 正确, content 是字符串."""
    msgs = build_messages("测试原文")
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert isinstance(msgs[0]["content"], str)
    assert isinstance(msgs[1]["content"], str)
    assert len(msgs[0]["content"]) > 100  # system prompt 是大块


def test_system_prompt_preserves_json_braces():
    """模板字面量的 JSON 花括号必须原样保留, 不能被替换吃掉."""
    content = build_messages("x")[0]["content"]
    # schema 块关键字段
    assert '"subject"' in content
    assert '"task_name"' in content
    assert '"due_date"' in content
    assert '"deadline_time"' in content
    assert '"is_optional"' in content
    assert '"est_minutes"' in content
    assert '"task_type"' in content
    assert '"source_span"' in content
    assert '"confidence"' in content
    # 场景 A 示例 JSON
    assert '{"task_name":"背诵 U2 指南重点短语和句子"' in content


def test_user_prompt_includes_raw_text():
    """用户原文必须出现在 user prompt 里 (不能让原文丢失)."""
    raw = "9.21 数学\n1. 订正"
    msgs = build_messages(raw)
    assert raw in msgs[1]["content"]


def test_today_and_weekday_substituted():
    """$today / $weekday 必须被替换, 不能留在 prompt 里."""
    from datetime import date

    msgs = build_messages("x", today=date(2026, 9, 22))
    assert "2026-09-22" in msgs[0]["content"]
    assert "周二" in msgs[0]["content"]
    # 重要: 占位符串本身不能残留 (说明替换成功)
    assert "$today" not in msgs[0]["content"]
    assert "$weekday" not in msgs[0]["content"]


def test_user_raw_text_with_dollar_sign_does_not_crash():
    """safe_substitute: 用户原文里若含 $variable, system prompt 仍要能构建 (system 不带 raw)."""
    # system 走 substitute (无 raw, 不会受影响)
    msgs = build_messages("价格 $100", today=__import__("datetime").date(2026, 9, 22))
    assert len(msgs) == 2
    # user 走 safe_substitute, $100 这种不被当成占位符
    assert "价格 $100" in msgs[1]["content"]


def test_user_raw_text_with_braces_only_definition_must_not_crash():
    """回归: 用户原文里若含 JSON-like 文本 (大括号), 必须不抛 KeyError."""
    # 之前 .format() 会把这里所有 {key} 当占位符
    tricky_raw = '今天作业: {"task_name": "完成 P14", "due_date": "2026-09-21"}'
    msgs = build_messages(tricky_raw)
    # 关键是 build_messages 没抛; 内容含字面包含即可
    assert tricky_raw in msgs[1]["content"]


def test_real_9_21_sample_builds():
    """真实 9.21 样本走 build_messages 必须不抛."""
    from tests.fixtures.real_notifications import NOTIFICATION_921, NOTIFICATION_922

    msgs = build_messages(NOTIFICATION_921)
    assert len(msgs) == 2
    assert NOTIFICATION_921 in msgs[1]["content"]
    msgs = build_messages(NOTIFICATION_922)
    assert msgs[1]["content"]  # 不空


def test_example_user_payload_runs():
    """example_user_payload 是 prompts 里的辅助函数, 也得能跑."""
    payload = example_user_payload()
    assert payload["model"] == "MiniMax-M3"
    assert payload["temperature"] == 0.2
    assert payload["response_format"] == {"type": "json_object"}
    assert isinstance(payload["messages"], list)
    assert len(payload["messages"]) == 2


def test_templates_are_string_template():
    """守住: 模板必须是 string.Template, 防止有人手贱改回 str 然后 .format() 炸."""
    # string.Template 的实例是 _TemplateMetaclass, 不是普通 str
    # 如果有人误改成 "..." 字符串, 这里 isinstance 会失败 -> 测试红
    # 注意: 我们用 Template() 包装后, 它的类型是 string.Template.Template
    import string

    assert isinstance(SYSTEM_PROMPT, string.Template)
    assert isinstance(USER_PROMPT_TEMPLATE, string.Template)


# ====== 场景 E: 独立作业项必须拆开 (2026-09-29 家长反馈) ======
# 家长原话: "完成知能 P18-19 并订正 2 听本" 被解析成 1 条, 应该拆成 2 条。
# 根因: 老 prompt 写了 "跨两步任务 ... 不要拆成两条", 把独立作业误当成连续步骤。

def test_prompt_does_not_forbid_splitting_two_step_tasks():
    """回归: 老的 "不要拆成两条" 规则必须消失, 否则独立作业又会被合并."""
    content = build_messages("x")[0]["content"]
    assert "不要拆成两条" not in content, "老规则『跨两步任务不要拆成两条』会重新引入合并 bug"
    assert "合并描述" not in content


def test_prompt_has_split_criterion_for_independent_items():
    """场景 E 必须给出可判定的拆分判据, 而不是模糊措辞."""
    content = build_messages("x")[0]["content"]
    # 用户报障的原句必须作为样例出现
    assert "完成知能 P18-19 并订正 2 听本" in content
    # 反向锚点: 同一交付物的必经步骤仍然合并, 防止过度拆分
    assert "必经步骤" in content
    # 判据: 右半句能否独立成一个作业名 (旧措辞「做完一项后是否还要做另一项」
    # 太主观, M3 实测仍会把"补全课堂笔记，并完成知能P20-21"合并, 已替换)
    assert "能不能独立成一个作业名" in content


def test_prompt_includes_2026_10_13_reported_sample():
    """2026-10-13 家长报障原句必须进 prompt 样例 (之前只有 9.29 那句).

    报障: "1.补全课堂笔记，并完成知能P20-21。" 应拆成 2 条, 实测被合并成 1 条。
    """
    content = build_messages("x")[0]["content"]
    assert "补全课堂笔记，并完成知能P20-21" in content
    # 默认拆, 不要默认合并 —— 这是本次报障的直接根因
    assert "默认拆" in content


def test_prompt_split_examples_are_consistent():
    """拆分示例里的 task_name 必须用 schema 允许的值 (other, 不是中文"其他")."""
    content = build_messages("x")[0]["content"]
    # schema 枚举里是 other; 示例里不能出现中文 "其他" 当 task_type
    assert '"task_type": "其他"' not in content
    assert '"task_type":"其他"' not in content


def test_real_split_sample_builds_without_error():
    """家长报障原句走 build_messages 必须不抛, 且原文完整进 prompt."""
    raw = "完成知能P18-19 并订正2听本"
    msgs = build_messages(raw, today=__import__("datetime").date(2026, 9, 29))
    assert len(msgs) == 2
    assert raw in msgs[1]["content"]
