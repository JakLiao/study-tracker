"""看板视觉密度的量化契约 (2026-10-12)

起因是用户反馈"视觉密度非常低, 纵向空间浪费严重, 要频繁滑动屏幕"。
这类问题不会让任何功能出错, 所以改版时很容易被顺手改回去 ——
新增一个 margin、一个字号, 没人会注意到首屏又少了两条作业。

这里把"省了多少"写成可量化的断言: 具体的 px 值、必须折叠的分区、
必须渲染的入口按钮。断言放在结构层而不是像素层, 留出后续微调的余量。

每条都标了为什么值得锁 —— 那些是实测发现的坑, 不是凭空设的门槛。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _read(p: str) -> str:
    return (ROOT / p).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return _read("app/static/app.js")


@pytest.fixture(scope="module")
def css() -> str:
    return _read("app/static/styles.css")


def _block(css: str, selector: str) -> str:
    """取一条规则的内容。selector 要带尾随空格以免匹配到前缀相同的选择器
    (.card 会命中 .card-title / .card-body)。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"styles.css 里找不到 {selector}"
    return m.group(1)


def _num(css: str, selector: str, prop: str) -> int:
    """读一个最终生效的 px 值。

    紧凑属性有两种写法: 直接给 px (padding: 6px 0), 或用设计变量
    (padding: var(--space-2))。后者必须回 :root 解析, 否则断言只能验证
    "用了变量"而验证不了"变量是多少" —— 而后者才是我们要锁的。
    """
    body = _block(css, selector)
    m = re.search(rf"{prop}:\s*(\d+)px", body)
    if m:
        return int(m.group(1))
    var = re.search(rf"{prop}:\s*var\((--[\w-]+)\)", body)
    assert var, f"{selector} 缺 {prop}"
    name = var.group(1)
    decl = re.search(rf"^\s*{re.escape(name)}:\s*(\d+)px\s*;", css, re.M)
    assert decl, f"{name} 没有具体 px 定义, 无法断言数值"
    return int(decl.group(1))


# ====== 1. 日期选择器: 分段控件 ======

def test_date_tabs_are_compact(css):
    """三张竖排方块(约 90px) 换成横排分段控件(约 30px)。

    分段控件的关键是"横排": 标题与进度同一行, 所以 display 不能再是
    flex-direction: column。
    """
    assert _num(css, ".date-tabs", "padding") == 2
    assert _num(css, ".date-tab", "padding") == 5
    block = css.split(".date-tab {")[1].split("}")[0]
    assert "flex-direction: column" not in block, "日期 Tab 回到了竖排方块"
    # 选中态改为填充块, 不再靠描边区分
    active = css.split(".date-tab.active {")[1].split("}")[0]
    assert "background: var(--accent)" in active


def test_date_tabs_keep_progress_visible(css):
    """压缩不能把"哪天做了几条"弄丢 —— 那是家长判断该不该翻这天的依据。"""
    assert "dt-count" in css
    assert "min-width: 84px" not in css, "旧的固定宽度仍在挤压"


# ====== 2. 概览卡: 四块合一 ======

def test_board_summary_is_one_card(js):
    """进度 / 鼓励语 / 学科用时合并进同一个 .progress-bar。

    原来是四块纵向堆叠(约 300px), 占掉 375px 屏的首屏一半。
    合并后一屏能同时读到"做到哪 / 用了多久 / 先做哪科"。
    """
    # 模板范围: 从 progress-bar 开标签到 progress-sub 那一行结束
    # (它就是模板里最后一行内容, 用它当锚点比找 </div> 稳)
    start = js.index('<div class="progress-bar">')
    end = js.index("\n      </div>", js.index("progress-sub", start))
    card = js[start:end]
    assert 'class="board-hint ' in card, "鼓励语没并入概览卡"
    assert 'class="subj-time-row"' in card, "学科用时没并入概览卡"
    assert 'class="progress-sub"' in card, "副行(完成/跳过/选做/估时)也没并入概览卡"
    assert 'class="progress-track"' in card
    # 必须是一张卡: 不能再有第二个顶层 progress-bar
    assert js.count('<div class="progress-bar">') == 1
    # 三块内容必须挂在同一次 appendChild 上 —— 分成多次 append 就等于
    # 又变回了纵向堆叠的独立块, "合并"只是嘴上说说。
    assert "appendChild" not in card, \
        "概览卡内部出现了额外的 appendChild, 说明内容被拆成了多块"


def test_effort_total_shares_the_progress_line(css):
    """总用时并入第一行, 不再独占一行。

    字号从 --fs-number-xl 降到 -l: 仍是成就级重量, 但不再为一个数字
    吃掉一行半的高度。
    """
    block = _block(css, ".effort-total")
    assert "margin-bottom" not in block, "总用时仍在独占一行"
    assert "display: inline-flex" in block
    num = _block(css, ".effort-total .et-num")
    assert "var(--fs-number-l)" in num, "字号没降, 数字仍会撑满一行"


def test_progress_sub_row_is_conditional(js):
    """副行(完成 n · 跳过 n · 选做 n/n · 估时 n 分)条件渲染。

    它是补充信息, 不该在完全没有跳过/选做的日子里占一行。
    """
    assert '${optNote || skippedNote ?' in js
    assert 'class="progress-sub"' in js


# ====== 3. 已收尾分区折叠 ======

def test_settled_section_is_collapsible(js):
    """「已收尾」默认折叠, 摘要显示 8/9 这类计数。

    实测: 9 条已收尾作业展开约 993px, 折叠后 46px —— 这是本次单项
    收益最大的一处。它是"回看"区, 而回看是低频动作。
    """
    assert 'section(\'已收尾\', settled, \'sec-settled\', false)' in js
    assert 'section(\'待完成\', pending, \'sec-pending\', true)' in js


def test_collapsible_card_has_expand_affordance(css):
    """折叠头必须看得出来能点: 去掉浏览器默认箭头, 换成自绘 ▾。"""
    assert "details.card > summary.card-title" in css
    assert "list-style: none" in css
    assert "details.card[open] > summary.card-title::after" in css, "展开后箭头应翻转"
    assert "cursor: pointer" in css


# ====== 4. 任务行密度 ======

def test_assignment_row_padding_is_tight(css):
    """行内上下内边距 8px -> 6px, gap 4px -> 3px。

    行间已有分隔线, 再留大片空白会让整屏放不下四条作业。
    """
    assert _num(css, ".assignment-row", "padding") == 6
    assert _num(css, ".assignment-row", "gap") == 3


def test_card_padding_is_tight(css):
    """卡片内边距 12px -> 8px。"""
    assert _num(css, ".card", "padding") == 8


def test_subject_tag_is_flattened(css):
    """科目标签压扁: 11px 字号 + 5px 内边距。

    每行左侧都要给它留位, 375px 屏上「语文」两个字原来要占 34px。
    白字对比度仍远超 4.5:1 (三个科目色都已压暗)。
    """
    assert _num(css, ".subject-tag", "font-size") == 11
    assert _num(css, ".subject-tag", "padding") == 1


def test_est_label_shares_line_with_number(css):
    """「预估」二字改为 inline, 与数字同行。

    原先 display:block 让每条作业的时长占两行 —— 但家长扫列表找的是
    "这条要花多久", 标签只是消歧, 配 11px 灰字足够。
    """
    block = css.split(".assignment-row .est-label {")[1].split("}")[0]
    assert "display: inline" in block
    assert "display: block" not in block


def test_pending_actual_uses_dot_not_banner(css):
    """「⚠ 请补实际耗时」整行文字改为行内小圆点。

    满屏重复同一句警告反而没人读。圆点贴左侧 —— 右侧那列是预估时长
    (右对齐), 贴右边会压在数字上(375px 实测)。
    """
    block = css.split(".assignment-row.pending-actual::before {")[1].split("}")[0]
    assert "请补实际耗时" not in block, "旧的一整行警告文字还在"
    assert "border-radius: 50%" in block
    assert "left:" in block and "right:" not in block, "圆点不能贴在预估时长那一列"


def test_empty_fill_container_does_not_take_row(css):
    """未展开时 .actual-fill 必须完全不占位。

    实测坑: 空 div 若给 width:100%, flex 仍把它当成一个换行单位,
    会把后面的「编辑」按钮挤到第三行, 行高从 44px 涨到 104px ——
    密度优化反而更稀疏了。
    """
    block = css.split(".assignment-row .actual-fill {")[1].split("}")[0]
    assert "display: contents" in block, "空容器仍在参与布局"
    assert ":not(:empty)" in css, "展开后必须恢复占位, 否则输入行会挤在一起"


def test_edit_button_is_icon_only(css):
    """「编辑」从虚线文字胶囊改为无边框铅笔图标。

    并排四五个虚线圆角时视觉噪音比"完成"的小红花还大。
    宽度从约 56px 降到 28px。
    """
    block = css.split(".status-btn.edit {")[1].split("}")[0]
    assert "dashed" not in block
    assert "transparent" in block
    assert "var(--status-overdue)" not in block, "编辑是纠错, 不是危险操作"


def test_fill_entry_button_stays_visible(js):
    """收起的是输入框, 不是入口 —— 「补用时」按钮必须一直在。"""
    code = "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))
    assert 'class="status-btn fill-actual">补用时' in code
    assert "<div class=\"actual-fill\"></div>" in code, "输入行不该常驻渲染"


def test_fill_panel_can_be_collapsed_again(js):
    """2026-10-12 用户反馈: 点开「补用时」后回不去了。

    根因是 expand() 把入口按钮删掉换成了输入行 —— 展开的唯一入口自己
    消失了, 家长只能刷新页面。同一个按钮必须在展开/收起两态间切换。

    断言要剥掉注释再比对: 修复说明里必然会提到"初版是删掉按钮"这件事,
    直接在原文里搜那个字面量会被注释自己命中。
    """
    code = "\n".join(l for l in js.splitlines() if not l.strip().startswith("//"))
    assert "fillBtn.remove()" not in code, "展开时删掉了入口按钮, 展开后无法收起"
    assert "const collapse" in code, "缺少收起分支"
    assert "fillRow.replaceChildren()" in code, \
        "收起时必须清空容器, 否则 CSS 的 :not(:empty) 不生效、行高回不去"
    # 文案切换: 收起态说"要做什么", 展开态说"怎么退出"
    assert "fillBtn.textContent = '取消'" in code
    assert "fillBtn.textContent = '补用时'" in code
    # 焦点交还: 输入框消失后焦点不能掉到 body
    assert "fillBtn.focus()" in code