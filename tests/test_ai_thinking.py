"""AI 请求契约: 出站 payload 的思考深度配置.

背景:
- 本项目的 AI 环节只做"把老师通知原文拆成结构化作业条目", 不需要多步推理.
- MiniMax-M3 省略 thinking 时默认开启 adaptive thinking, 会额外产出 reasoning_content,
  更慢更贵, 且模型有机会在正文里写非 JSON 铺垫. 因此显式关闭.
- OpenAI 那套 reasoning_effort=low|medium|high 属于 M3.1-Flash-Preview 等模型,
  MiniMax-M3 的 Chat Completions 不认这个字段, 传了也不降档.

这里直接断言 requests.post 收到的 payload, 是为了让"关掉思考"这个决定变成可回归的契约,
而不是一条谁都能随手改掉的注释.
"""
from __future__ import annotations

import json

import pytest
import requests

from app import ai_client


# ====== 测试替身 ======

class _FakeResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


def _ok_resp(items: list[dict], *, reasoning: str | None = None) -> _FakeResp:
    """构造一个 OpenAI 兼容的成功响应.

    reasoning 非 None 时附带 reasoning_content, 用来确认我们不会去读它.
    """
    message = {"role": "assistant", "content": json.dumps(items, ensure_ascii=False)}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return _FakeResp({
        "model": "MiniMax-M3",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    })


@pytest.fixture
def ai_ready(monkeypatch):
    """配置好 AI Key 并 reload config, 让 call_minimax 能走到发请求这一步."""
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-fake-key-for-test-only")
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)
    yield cfg_mod


@pytest.fixture
def captured_post(monkeypatch):
    """拦截 requests.post, 返回记录了调用参数的对象."""
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None, **kw):
        calls.append({"url": url, "headers": headers, "payload": json, "timeout": timeout})
        return _ok_resp([{"subject": "语文", "task_name": "完成知能 P14", "source_span": "完成知能P14"}])

    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    monkeypatch.setattr(ai_client._time, "sleep", lambda *_a, **_k: None)
    return calls


# ====== 核心: 思考深度 ======

def test_payload_disables_thinking(ai_ready, captured_post):
    """必须显式关闭思考深度."""
    ai_client.call_minimax("完成知能 P14")
    assert len(captured_post) == 1
    assert captured_post[0]["payload"]["thinking"] == {"type": "disabled"}


def test_payload_does_not_send_reasoning_effort(ai_ready, captured_post):
    """M3 不认 reasoning_effort, 传了也不降档, 不能拿它冒充"最低思考深度"."""
    ai_client.call_minimax("完成知能 P14")
    payload = captured_post[0]["payload"]
    assert "reasoning_effort" not in payload


def test_payload_keeps_low_temperature(ai_ready, captured_post):
    """关思考不等于放弃抽取稳定性, temperature 必须留在低档."""
    ai_client.call_minimax("完成知能 P14")
    payload = captured_post[0]["payload"]
    assert payload["temperature"] <= 0.5


def test_payload_thinking_type_is_valid_value(ai_ready, captured_post):
    """M3 只认 disabled / adaptive 两个值, 防止写错字符串导致静默失效."""
    ai_client.call_minimax("完成知能 P14")
    thinking = captured_post[0]["payload"]["thinking"]
    assert isinstance(thinking, dict)
    assert thinking.get("type") in ("disabled", "adaptive")
    assert thinking["type"] == "disabled"


# ====== 关闭思考后, 响应处理仍然正确 ======

def test_parses_plain_content_without_think_tags(ai_ready, monkeypatch):
    """thinking 关闭后响应里没有 <think>, 干净 JSON 必须能直接解析."""
    monkeypatch.setattr(
        ai_client.requests, "post",
        lambda *a, **k: _ok_resp([{"subject": "数学", "task_name": "订正 P10-11", "source_span": "订正P10-11"}]),
    )
    result = ai_client.call_minimax("订正 P10-11")
    assert "<think>" not in result["content"]
    items = ai_client.parse_ai_response(result["content"])
    assert len(items) == 1
    assert items[0]["subject"] == "数学"


def test_ignores_reasoning_content_field(ai_ready, monkeypatch):
    """响应里即使带 reasoning_content, 也只取 content 拆条目."""
    monkeypatch.setattr(
        ai_client.requests, "post",
        lambda *a, **k: _ok_resp(
            [{"subject": "英语", "task_name": "背诵 P18-19", "source_span": "背课文P18-19"}],
            reasoning="先找科目, 再抽日期...",
        ),
    )
    result = ai_client.call_minimax("背诵 P18-19")
    assert "先找科目" not in result["content"]
    items = ai_client.parse_ai_response(result["content"])
    assert len(items) == 1


def test_still_handles_json_fence(ai_ready, monkeypatch):
    """模型偶尔仍会包 ```json 围栏, 兜底逻辑不能因为改 thinking 被删掉."""
    fenced = '```json\n[{"subject":"语文","task_name":"阅读","source_span":"阅读"}]\n```'
    monkeypatch.setattr(
        ai_client.requests, "post",
        lambda *a, **k: _FakeResp({"model": "MiniMax-M3", "choices": [{"message": {"content": fenced}}]}),
    )
    result = ai_client.call_minimax("阅读")
    items = ai_client.parse_ai_response(result["content"])
    assert len(items) == 1


def test_falls_back_when_think_tags_leak_into_content(ai_ready, monkeypatch):
    """兜底: 万一 thinking 标签仍混进正文, 也不能整段报废."""
    leaky = '<think>模型自述</think>\n[{"subject":"数学","task_name":"预习","source_span":"预习"}]'
    monkeypatch.setattr(
        ai_client.requests, "post",
        lambda *a, **k: _FakeResp({"model": "MiniMax-M3", "choices": [{"message": {"content": leaky}}]}),
    )
    result = ai_client.call_minimax("预习")
    items = ai_client.parse_ai_response(result["content"])
    assert len(items) == 1
    assert items[0]["task_name"] == "预习"


# ====== 未受影响的既有行为 (回归护栏) ======

def test_no_key_makes_no_request(monkeypatch):
    """Key 缺失时必须直接短路, 不能发请求."""
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    import importlib
    import app.config as cfg_mod
    importlib.reload(cfg_mod)

    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("Key 缺失时不应发起请求")

    monkeypatch.setattr(ai_client.requests, "post", boom)
    result = ai_client.call_minimax("任意文本")
    assert called["n"] == 0
    assert result["ok"] is False
    assert result["error"]["code"] == "AI_KEY_MISSING"


def test_timeout_retries_then_structured_error(ai_ready, monkeypatch):
    """超时仍是 1 次重试 + 结构化错误, 不抛异常."""
    attempts = {"n": 0}

    def always_timeout(*a, **k):
        attempts["n"] += 1
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(ai_client.requests, "post", always_timeout)
    monkeypatch.setattr(ai_client._time, "sleep", lambda *_a, **_k: None)

    result = ai_client.call_minimax("任意文本")
    assert attempts["n"] == 2  # 首次 + 1 次重试
    assert result["ok"] is False
    assert result["error"]["code"] == "AI_TIMEOUT"


def test_retry_payload_keeps_thinking_disabled(ai_ready, monkeypatch):
    """重试必须复用同一个 payload, 不能某次悄悄把思考打开."""
    payloads = []

    def flaky(*a, **k):
        payloads.append(k.get("json"))
        if len(payloads) == 1:
            raise requests.exceptions.ConnectionError()
        return _ok_resp([{"subject": "语文", "task_name": "订正", "source_span": "订正"}])

    monkeypatch.setattr(ai_client.requests, "post", flaky)
    monkeypatch.setattr(ai_client._time, "sleep", lambda *_a, **_k: None)

    result = ai_client.call_minimax("订正")
    assert result.get("ok") is not False
    assert len(payloads) == 2
    for p in payloads:
        assert p["thinking"] == {"type": "disabled"}


def test_empty_content_still_structured_error(ai_ready, monkeypatch):
    monkeypatch.setattr(
        ai_client.requests, "post",
        lambda *a, **k: _FakeResp({"model": "MiniMax-M3", "choices": [{"message": {"content": "  "}}]}),
    )
    result = ai_client.call_minimax("任意文本")
    assert result["ok"] is False
    assert result["error"]["code"] == "AI_FORMAT"
