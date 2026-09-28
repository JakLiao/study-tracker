"""选做分组 + 手动添加弹窗表单 的视觉契约.

2026-09-28 两条用户反馈:
1. 看板底部「选做」默认展开, 且底色不要黄色高亮 —— 选做的定义就是
   优先级低, 用高饱和底色反而把它顶成视觉焦点, 与意图相反。
2. 手动添加弹窗的表单没对齐 —— 标签被输入框挤成「科/目」竖排。

这两处都是纯样式, 很容易在后续改版时被无意改回去, 所以用可量化
的断言(色值/属性存在性)锁住, 而不是靠人眼复查。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def _var(styles: str, name: str) -> str:
    m = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{3,8}})", styles)
    assert m, f"未找到 CSS 变量 --{name}"
    return m.group(1)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(hex_color: str) -> float:
    def ch(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hex_color)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ====== 1. 选做底色改成中性灰 ======

def test_optional_bg_is_neutral_grey(styles):
    """底色必须是低饱和的灰, 不能再是琥珀黄。

    判定用饱和度而不是"看起来像不像灰" —— RGB 三通道最大值与最小值
    之差小即低饱和, 黄色底这个差值会很大。
    """
    bg = _var(styles, "row-opt-bg")
    r, g, b = _rgb(bg)
    spread = max(r, g, b) - min(r, g, b)
    assert spread <= 12, f"{bg} 饱和度过高(通道差 {spread}), 选做底色应为中性灰"


def test_optional_bg_is_light(styles):
    """必须是浅色, 深灰会让选做变成最重的一块。"""
    bg = _var(styles, "row-opt-bg")
    assert _luminance(bg) > 0.7, f"{bg} 过暗, 选做行应保持浅底"


def test_optional_bg_still_distinct_from_white(styles):
    """再淡也要能和白卡片分开, 否则选做与必做完全无法区分。"""
    bg = _var(styles, "row-opt-bg")
    assert _contrast(bg, "#FFFFFF") >= 1.04, f"{bg} 与白色几乎无差异, 分不出选做"


def test_optional_text_still_readable(styles):
    """改成浅灰后正文对比度必须够 —— 变淡不能变成看不清。"""
    bg = _var(styles, "row-opt-bg")
    assert _contrast("#222222", bg) >= 4.5, f"正文在 {bg} 上对比度不足"


def test_optional_no_longer_overrides_text_brightness(styles):
    """撤回"把选做行文字提亮到 --text-1"的补偿。

    那条规则当初是为在深琥珀底上保住对比度。底色变浅后它反而有害 ——
    选做行的次要文字会比必做行更黑, 与"选做不显眼"完全相反。
    """
    block = re.search(
        r"\.assignment-row\.is-optional \.muted,[\s\S]*?\{([^}]*)\}", styles)
    assert block, "未找到选做行文字规则"
    assert "var(--text-2)" in block.group(1), \
        "选做行次要文字不应再被提到 --text-1, 那会让它比必做行更显眼"
    assert "var(--text-1)" not in block.group(1)


# ====== 2. 选做分组默认展开 ======

def test_optional_card_default_open(app_js):
    """看板的选做分组必须带 open, 默认展开。"""
    m = re.search(r'<details class="card optional-card"[^>]*>', app_js)
    assert m, "未找到选做分组的 details 元素"
    assert "open" in m.group(0), "选做分组缺少 open, 默认仍是收起"


def test_all_optional_cards_open(app_js):
    """看板和日历详情两处都必须展开 —— 同一份数据两个界面表现不同,
    家长会怀疑其中一边显示错了。"""
    cards = re.findall(r'<details class="card optional-card"[^>]*>', app_js)
    assert len(cards) == 2, f"应有两处选做分组, 实际 {len(cards)}"
    for c in cards:
        assert "open" in c, f"选做分组未默认展开: {c}"


# ====== 3. 弹窗表单网格对齐 ======

def test_manual_form_uses_grid(styles):
    """表单用两列网格: 标签固定一列, 控件占另一列。"""
    m = re.search(r"\.form-grid \{(.*?)\}", styles, re.S)
    assert m, "缺少 .form-grid 定义"
    block = m.group(1)
    assert "display: grid" in block
    assert "grid-template-columns" in block


def test_form_label_column_fixed_width(styles):
    """标签列必须是固定宽度, 否则又会被输入框挤压成竖排单字。"""
    m = re.search(r"\.form-grid \{(.*?)\}", styles, re.S)
    cols = re.search(r"grid-template-columns:\s*(\d+)px\s+minmax\(0,\s*1fr\)", m.group(1))
    assert cols, f"标签列未固定宽度: {m.group(1)[:120]}"
    # 「截止时间」四字约 56px, 加上内边距, 低于 64px 就会折行
    assert int(cols.group(1)) >= 64, f"标签列 {cols.group(1)}px 放不下四字标签"


def test_form_label_no_wrap(styles):
    """标签文字禁止换行 —— 这是"竖排成科/目"的直接防线。"""
    m = re.search(r"\.form-grid \.form-label \{(.*?)\}", styles, re.S)
    assert m, "缺少 .form-label 定义"
    assert "white-space: nowrap" in m.group(1)


def test_form_inputs_fill_column(styles):
    """控件铺满所在列, 且 min-width:0 防止日期框固有宽度撑破网格。"""
    m = re.search(r"\.form-grid input:not\(\[type=\"checkbox\"\]\),\s*\.form-grid select \{(.*?)\}",
                  styles, re.S)
    assert m, "缺少 .form-grid 控件宽度规则"
    block = m.group(1)
    assert "width: 100%" in block
    assert "min-width: 0" in block, "缺 min-width:0, 日期框会把网格撑破"


def test_manual_form_labels_marked(app_js):
    """每个字段都要有 .form-label 标签文字, 否则网格第一列空着。"""
    block = re.search(r'<div class="form-grid">(.*?)</div>', app_js, re.S)
    assert block, "未找到 .form-grid 表单"
    html = block.group(1)
    for text in ("科目", "预计用时", "任务名", "归属日", "截止时间"):
        assert f'<span class="form-label">{text}</span>' in html, f"{text} 缺少标签"


def test_manual_form_all_fields_present(app_js):
    """五个字段的 id 不能因改布局丢失 —— 提交逻辑按 id 取值。"""
    block = re.search(r'<div class="form-grid">(.*?)</div>', app_js, re.S)
    html = block.group(1)
    for fid in ("m-subject", "m-est", "m-name", "m-due", "m-dl", "m-opt"):
        assert f'id="{fid}"' in html, f"{fid} 丢失, 提交会取不到值"


def test_manual_form_date_pickers_kept(app_js):
    """日期框必须仍带组件类, 否则弹窗里会露出难看的原生控件。"""
    assert 'id="m-due" type="date" class="js-datepicker"' in app_js
    assert 'id="m-dl" type="datetime-local" class="js-datetimepicker"' in app_js


def test_est_input_width_bounded(styles):
    """数字输入框限宽, 不铺满整列 —— 400px 宽的数字框很怪。"""
    m = re.search(r"\.form-grid input\[type=\"number\"\] \{(.*?)\}", styles, re.S)
    assert m, "预计用时未限宽"
    mx = re.search(r"max-width:\s*(\d+)px", m.group(1))
    assert mx and int(mx.group(1)) <= 140, f"限宽 {mx.group(1) if mx else '?'}px 仍过宽"
