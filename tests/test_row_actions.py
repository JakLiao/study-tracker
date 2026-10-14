"""按钮语义化 + 用词统一 + 重做功能的回归测试 (2026-09-28).

用户截图批注了四件事:
  1. 放大"完成"按钮
  2. 缩小"今天不做/选做/删除"等次要按钮
  3. "实耗"用词不对
  4. 已完成的部分增加"重做"撤回功能

这里既锁后端状态流转 (重做的前提), 也锁前端文案与样式契约。
"""
from __future__ import annotations

import re

import pytest


def _item(**overrides):
    item = {
        "subject": "语文",
        "task_name": "测试作业",
        "due_date": "2026-09-21",
        "deadline_time": None,
        "is_optional": False,
        "est_minutes": 30,
        "task_type": "homework",
        "source_span": "(测试)",
        "confidence": "high",
    }
    item.update(overrides)
    return item


def _seed(client, *items):
    r = client.post("/api/assignments/batch", json={"batch_id": None, "items": list(items)})
    assert r.status_code == 200, r.text
    return r.json()


# ====== 后端: 重做 (done -> todo) 必须可行 ======

def test_redo_can_return_done_to_todo(app_client):
    """用户批注 4: 已完成的项要能撤回。

    没有这条, 家长点错一次就只能去数据库手改。
    """
    ids = _seed(app_client, _item())["created_ids"]
    aid = ids[0]
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    r = app_client.patch(f"/api/assignments/{aid}/status", json={"status": "todo"})
    assert r.status_code == 200
    assert r.json()["status"] == "todo"


def test_redo_clears_actual_minutes(app_client):
    """退回未完成时必须清掉用时, 否则出现"待办却显示用了 30 分"的矛盾。"""
    ids = _seed(app_client, _item())["created_ids"]
    aid = ids[0]
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{aid}/completion", json={"actual_minutes": 32})

    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "todo"})
    got = app_client.get(f"/api/assignments?date=2026-09-21").json()["items"][0]
    assert got["status"] == "todo"
    assert got["actual_minutes"] is None


def test_redo_then_complete_again(app_client):
    """重做后应能重新完成 (走完整的 pending_actual 流程)。"""
    ids = _seed(app_client, _item())["created_ids"]
    aid = ids[0]
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "todo"})
    r = app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"


def test_board_progress_reflects_redo(app_client):
    """重做后进度应回退, 否则家长看到"已完成 1/5"但列表里是空的。"""
    ids = _seed(app_client,
                _item(task_name="a", subject="语文"),
                _item(task_name="b", subject="数学"))["created_ids"]
    aid = ids[0]
    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "done"})

    board = app_client.get("/api/board?date=2026-09-21").json()
    p = board.get("progress") or {}
    assert p.get("done") == 1

    app_client.patch(f"/api/assignments/{aid}/status", json={"status": "todo"})
    board = app_client.get("/api/board?date=2026-09-21").json()
    p = board.get("progress") or {}
    assert p.get("done") == 0


def test_redo_does_not_affect_other_items(app_client):
    """重做一条不能波及同一天其它作业。"""
    ids = _seed(app_client,
                _item(task_name="a", subject="语文"),
                _item(task_name="b", subject="数学"))["created_ids"]
    app_client.patch(f"/api/assignments/{ids[0]}/status", json={"status": "done"})
    app_client.patch(f"/api/assignments/{ids[0]}/status", json={"status": "todo"})
    items = app_client.get("/api/assignments?date=2026-09-21").json()["items"]
    assert {i["status"] for i in items} == {"todo"}


def test_invalid_redo_status_rejected(app_client):
    ids = _seed(app_client, _item())["created_ids"]
    r = app_client.patch(f"/api/assignments/{ids[0]}/status", json={"status": "done_xx"})
    assert r.status_code == 400


# ====== 前端契约 (静态检查) ======

@pytest.fixture(scope="module")
def app_js() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "app/static/app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "app/static/styles.css").read_text(encoding="utf-8")


def _code(js: str) -> str:
    """去掉注释行, 避免注释里的文字被当成实现。"""
    return "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))


def test_redo_button_exists(app_js):
    assert "status-btn redo" in app_js
    assert "重做" in app_js


def test_redo_only_for_done_items(app_js):
    """重做是 done 的分支, 不能和 todo 的"完成"同时出现。"""
    code = _code(app_js)
    assert re.search(r"item\.status === 'done'", code)
    assert "'todo'" in code


def test_redo_targets_todo(app_js):
    assert "status: 'todo'" in _code(app_js)


def test_no_stale_shihan(app_js):
    """用户批注 3: "实耗"这个词要换掉。"""
    code = _code(app_js)
    assert "实耗" not in code, "界面文案不应再出现实耗"
    assert "保存实耗" not in code
    assert "改实耗" not in code


def test_wording_is_consistent(app_js):
    """同一个概念 (actual_minutes) 在界面上只能有一种叫法。

    2026-09-29 用户批注: pending_actual 的保存按钮文案由「保存用时」
    缩成「保存」——输入框 placeholder 已写明「填实际用时」, 按钮再重复
    一遍是冗余, 且四按钮并排时「保存用时」会把行宽顶开。
    这里只锁"用时"这个概念名不再退化回「实耗」, 不再要求按钮带全称。
    """
    code = _code(app_js)
    assert "改用时" in code
    assert "实际用时" in code
    # 保存按钮已缩短, 但仍必须是「保存」二字, 不能退回带全称的长文案
    assert "保存用时" not in code, "按钮文案应保持简短「保存」"
    assert '<button class="primary">保存</button>' in code


def test_est_minutes_has_label(app_js):
    """光秃秃一个数字分不清是估的还是实际的, 必须带"预估"标签。"""
    assert 'class="est-label">预估<' in app_js


def test_actual_tag_shown_when_filled(app_js):
    assert "actual-tag" in app_js
    assert "actual_minutes != null" in app_js


def test_actual_input_prefilled_with_est(app_js):
    """2026-10-14 用户反馈: 点完「完成」还要从空框里敲一遍预估的数字。

    家长心里的默认答案就是预估值, 空框等于强迫他重打一遍自己刚看到的
    那个数。展开时必须预填, 并且全选 —— 全选是为了"想改时直接敲数字
    覆盖", 不用先按删除键清掉预填值。
    """
    m = re.search(r"const expand = \(\) => \{(.*?)\n    \};", app_js, re.S)
    assert m, "找不到 expand()"
    body = m.group(1)
    assert re.search(r"if \(item\.est_minutes != null\) input\.value = item\.est_minutes;", body), \
        "展开时未预填预估值"
    assert "input.select()" in body, "预填后必须全选, 否则改数字得先手动清空"
    # 预估值可能为 null(手动添加时没填), 不能把 null 写进 input
    assert "input.value = item.est_minutes;" in body and \
        "input.value = item.est_minutes;" not in body.replace(
            "if (item.est_minutes != null) input.value = item.est_minutes;", ""), \
        "est_minutes 为 null 时不能给输入框赋 null"


def test_prefill_is_not_autosaved(app_js):
    """预填只是填进输入框, 不能静默当成实测值提交。

    周复盘的「预估 vs 实际」偏差全靠 actual_minutes 与 est_minutes 的
    差值; 若点完成就自动写入预估值, 偏差恒为 0, 这张表就废了。
    """
    expand_body = re.search(r"const expand = \(\) => \{(.*?)\n    \};", app_js, re.S).group(1)
    assert "api(" not in expand_body, "expand() 不该发请求, 预填≠提交"


def test_actual_input_placeholder_not_truncated(app_js, styles_css):
    """用户批注 (2026-09-29): "填实际用时" 5 个字要看全, 别被切掉。

    占位符是 5 个汉字, 在 --fs-body-s (14px) 下约需 70px, 加左右内边距
    与边框约 96px。宽度必须由 CSS 统一给, 不能再用 inline style 写死
    —— 否则它会盖掉全局 input[type=number] 规则, 改样式时最容易漏改。
    """
    assert 'placeholder="填实际用时"' in app_js
    # 宽度不再 inline 硬编码, 改由 styles.css 接管
    assert "actual-input" in app_js
    assert 'style="width:90px;"' not in app_js, "输入框宽度应交给 CSS, 别 inline 写死"
    # CSS 里必须有专门给 .actual-input 的宽度, 且放得下 5 个汉字
    m = re.search(r"\.assignment-row \.actual-input\s*\{\s*width:\s*(\d+)px", styles_css)
    assert m, "styles.css 应显式给 .actual-input 设宽度"
    assert int(m.group(1)) >= 96, "宽度需容得下 5 个汉字占位符 + 内边距"


# ====== 样式契约 ======

def test_done_button_is_enlarged(styles_css):
    """用户批注 1: 放大完成按钮。"""
    block = styles_css.split(".status-btn.done {")[1].split("}")[0]
    assert "min-height: 34px" in block
    assert "font-weight: 700" in block
    assert "box-shadow" in block


def test_secondary_buttons_are_smaller(styles_css):
    """用户批注 2: 缩小次要按钮。基础档必须小于 done 的 34px。"""
    base = styles_css.split(".status-btn {")[1].split("\n}")[0]
    m = re.search(r"min-height:\s*(\d+)px", base)
    assert m, "基础档应显式设置 min-height"
    assert int(m.group(1)) < 34, "次操作应明显小于主操作"


def test_secondary_buttons_respect_min_target(styles_css):
    """但不能缩到点不准: 守住 22px 下限。"""
    base = styles_css.split(".status-btn {")[1].split("\n}")[0]
    m = re.search(r"min-height:\s*(\d+)px", base)
    assert int(m.group(1)) >= 22, "低于 22px 在触屏上很难命中"


def test_skipped_no_longer_solid_green_red(app_js, styles_css):
    """"今天不做"降为描边式, 不再和"完成"的实心块正面竞争。"""
    block = styles_css.split(".status-btn.skipped {")[1].split("}")[0]
    assert "background: var(--surface)" in block, "应是描边而非实心"
    assert "color: #A93226" in block, "警示语义靠深红文字保留"


def test_skipped_red_contrast(styles_css):
    """#A93226 在白底上 6.0:1, 达 AA。"""
    assert "#A93226" in styles_css


def test_redo_is_not_a_second_primary(styles_css):
    """重做是补救操作, 用中性灰, 不用实心色块。"""
    block = styles_css.split(".status-btn.redo {")[1].split("}")[0]
    assert "dashed" in block
    assert "var(--text-2)" in block


def test_deletion_uses_small_size(styles_css):
    """删除按钮要跟随次操作档尺寸 (否则它仍是默认大按钮)。"""
    assert "status-btn danger" in _code(styles_css) or ".status-btn.danger" in styles_css
    assert ".status-btn.danger" in styles_css


def test_no_duplicate_properties_in_status_btn(styles_css):
    """.status-btn 里不应重复声明 padding / font-size (之前改崩过一次)。"""
    base = styles_css.split(".status-btn {")[1].split("\n}")[0]
    assert len(re.findall(r"^\s*padding:", base, re.M)) <= 1
    assert len(re.findall(r"^\s*font-size:", base, re.M)) <= 1


def test_caption_sm_token_defined(styles_css):
    """新用到的字号变量必须在 :root 里有定义。"""
    assert "--fs-caption-sm:" in styles_css
    assert "var(--fs-caption-sm, 11px)" not in styles_css, \
        "变量已定义, 不该再写 fallback"


def test_est_label_styled(styles_css):
    assert ".assignment-row .est-label" in styles_css
    assert "display: block" in styles_css


def test_actual_tag_styled(styles_css):
    assert ".assignment-row .actual-tag" in styles_css
