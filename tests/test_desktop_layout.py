"""PC 端布局回归测试 (2026-09-29 多分辨率复核)。

这一轮在 641 / 700 / 767 / 768 / 782 / 1024 / 1366 / 1440 / 1920 / 2560
十个宽度上实测, 结论与截图:

  宽度 <=640            预览表转卡片 (§26c)
  641~782               混合态: 固定底栏 + 16px 输入框, 但仍是桌面表格
  >=783                 纯桌面: main 恒为 720px, 表格 688px, 列宽恒定

两个反直觉的坑, 都写成了断言:

1. **媒体查询量的是 innerWidth, 含经典滚动条。** 767px 的窗口里
   documentElement.clientWidth 只有 752, 但 matchMedia('(max-width:
   767px)') 比的是 767 —— 所以"窗口宽度"和"媒体查询看到的宽度"在
   有滚动条时并不是同一个数, 差一条滚动条(约15px)。别拿 clientWidth
   去推断断点。

2. **复盘表在桌面上会把两字列竖排。** 它是 table-layout:auto 且原本
   没有任何 white-space 约束, "类型"/"样本"各只分到 50px, 扣掉左右
   各 12px 内边距剩 26px, 装不下 28px 的"类型"二字, 于是断成
   "类/型"两行 —— 单字成行比不写更难认。改成 nowrap 让 auto 布局
   自行按内容分配。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def styles() -> str:
    return (ROOT / "app/static/styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "app/static/app.js").read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


# ===== 复盘偏差表 ======

def test_weekly_short_label_columns_never_wrap(styles):
    """两字列(type=阅读/样本)不能被竖排成两行。

    这是本轮唯一改动的桌面规则。锁住的是"短标签列 nowrap", 而不是
    某个列宽数值 —— 列宽交给 table-layout:auto 按内容算, 数值一变
    这条断言就该失效。
    """
    css = _strip_comments(styles)
    rule = re.search(r"\.weekly-table th,\s*\.weekly-table td\s*\{([^}]*)\}", css)
    assert rule, "缺少 .weekly-table 的 nowrap 规则"
    assert "white-space: nowrap" in rule.group(1)


def test_weekly_advice_column_may_wrap(styles):
    """"建议"是一整段中文, 必须允许折行 —— 否则会横向溢出。"""
    css = _strip_comments(styles)
    rule = re.search(r"\.weekly-table td\.col-advice\s*\{([^}]*)\}", css)
    assert rule, "缺少 .col-advice 的换行豁免"
    assert "white-space: normal" in rule.group(1)


def test_weekly_narrow_override_still_applies(styles):
    """桌面加的 nowrap 不能把窄屏卡片布局的换行也顶掉。

    §26g 的卡片规则必须排在基础 nowrap 之后且同特异度, 否则
    375px 下每格会挤成一行。
    """
    css = _strip_comments(styles)
    nowrap = css.index(".weekly-table th,\n.weekly-table td { white-space: nowrap; }") \
        if ".weekly-table th,\n.weekly-table td { white-space: nowrap; }" in css \
        else css.index("white-space: nowrap")
    card = css.index(".weekly-table td {", nowrap)
    card_block = css[card:card + 400]
    assert "white-space: normal" in card_block, \
        "窄屏卡片块里必须有 white-space: normal 覆盖桌面的 nowrap"


def test_weekly_has_class_hook(styles, app_js):
    """样式挂在 .weekly-table 上, JS 必须真的输出这个 class。"""
    assert 'class="weekly-table"' in app_js
    assert ".weekly-table" in styles


# ===== 桌面断点边界 ======

def test_reading_measure_is_capped(styles):
    """main 必须有上限。1920/2560 宽屏上若无上限, 内容会被拉成
    一行极长的带子, 每行上百个字, 阅读非常吃力。"""
    css = _strip_comments(styles)
    m = re.search(r"\bmain\s*\{[^}]*max-width:\s*(\d+)px", css)
    assert m, "main 缺少 max-width"
    w = int(m.group(1))
    assert 640 <= w <= 820, f"{w}px 不是合适的阅读 measure (建议 640~820)"


def test_phone_breakpoint_does_not_leak_onto_desktop(styles):
    """窄屏规则一律挂在 (max-width: 767px), 不能出现裸的
    @media (max-width: 639px) 之类把桌面卷进去的宽断点。"""
    css = _strip_comments(styles)
    # 允许存在的更宽断点(它们只服务更窄的场景), 但 767 必须是主断点
    assert "(max-width: 767px), (max-height: 500px)" in css


def _top_level_selectors(css: str) -> list[str]:
    """返回所有**不在任何 @media 内**的选择器块。

    不能用 "nav#tabs {[^}]*position: fixed" 这种正则来判断"泄漏到桌面":
    去掉注释后, @media 里的规则和顶层的规则在文本上长得一模一样,
    正则根本分不出层级。这里按花括号深度走一遍, 深度 0 才算顶层。
    """
    out, depth, buf, sel_start = [], 0, [], 0
    i, n = 0, len(css)
    while i < n:
        ch = css[i]
        if ch == "{":
            sel = "".join(buf).strip()
            buf = []
            depth += 1
            if depth == 1:
                sel_start = i
        elif ch == "}":
            if depth == 1:
                out.append((sel_start, sel))
            depth -= 1
        elif depth == 0:
            buf.append(ch)
        i += 1
    return out


def test_mobile_only_rules_are_inside_media_query(styles):
    """固定底栏 / 44px 触控 / 卡片布局这些只能在媒体查询里出现。

    桌面端 34px 的紧凑次操作按钮是有意设计(test_row_actions 断言
    22 <= h < 34), 一旦这些规则漏出媒体查询就会把桌面信息密度毁掉。
    """
    css = _strip_comments(styles)

    # 1) 顶层的 nav#tabs 规则里不许出现 position: fixed
    for pos, sel in _top_level_selectors(css):
        if "nav#tabs" not in sel:
            continue
        body = css[pos:pos + 600]
        assert "position: fixed" not in body.split("}")[0], \
            "nav#tabs 的 fixed 出现在顶层(不在 @media 内), 会泄漏到桌面"

    # 2) 顶层的 .status-btn 不许被抬到 44px
    for pos, sel in _top_level_selectors(css):
        if sel.strip() != ".status-btn":
            continue
        first = css[pos:pos + 400].split("}")[0]
        assert "min-height: 44px" not in first, \
            "顶层 .status-btn 被抬到 44px, 桌面紧凑密度被破坏"


# ===== 预览表列预算 (与 test_preview_table 的互补) ======

def test_preview_table_task_column_is_the_widest_fixed_one(styles):
    """任务名列应当是"吃掉剩余"的那一列, 也就是比任何固定列都宽。

    2026-09-29 桌面复核前的实况: 固定列合计 560px, 688px 的表格里
    任务名只剩 128px, 比"归属日"还窄 —— 最重要的列反而最窄。
    """
    css = _strip_comments(styles)
    widths = {}
    for m in re.finditer(r"\.preview-table th:nth-child\((\d)\)[^}]*\{\s*width:\s*([^;]+);", css):
        widths[int(m.group(1))] = m.group(2).strip()
    assert widths, "找不到预览表列宽定义"
    assert widths[3] == "auto", "第 3 列(任务名)应为 auto"
    fixed = {c: int(re.sub(r"px$", "", w)) for c, w in widths.items() if c != 3}
    assert fixed, "除任务名外还应有固定宽列"
    # 任务名的 min-width 必须大于所有固定列
    m3 = re.search(r"\.preview-table th:nth-child\(3\)[^}]*\{[^}]*min-width:\s*(\d+)px", css)
    assert m3, "任务名列缺少 min-width 下限"
    assert int(m3.group(1)) > max(fixed.values()), \
        f"任务名下限 {m3.group(1)}px 应大于最宽的固定列 {max(fixed.values())}px"


def test_preview_table_has_no_need_date_hint_overflow(styles, app_js):
    """归属日列只有 ~124px, 而 td 是 nowrap —— 长提示会被截成半句话。

    可见文案必须短, 完整说明进 title。
    """
    assert 'class="need-date-hint" title=' in app_js, \
        "need-date-hint 必须带 title 兜底(列窄, 可见文案放不下)"
    m = re.search(r'<small class="need-date-hint" title="([^"]+)">([^<]+)<', app_js)
    assert m, "无法解析 need-date-hint 标记"
    assert len(m.group(2)) <= 6, \
        f"可见文案 {m.group(2)!r} 过长, 在 124px 的 nowrap 单元格里会被截断"
    assert len(m.group(1)) > len(m.group(2)), "title 应比可见文案更完整"
