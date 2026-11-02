"""周计划页改版: 视图切换器 + 页宽豁免 + 7 列周视图 (2026-11-02).

用户反馈「前几天做的课外课程管理的功能, 当前顶部菜单已经见不到了」。
根因是两层:

  1. 2026-11-01 双角色改造把顶部 6 Tab 砍到 3 个, 周计划退成
     display:none 的深链落点, 只剩日历页右上角一个 ghost 灰色小字;
  2. styles.css 的 ``main { max-width: 720px }`` 把周计划页的内容列
     卡在 688px —— 1280 视口下右边空 560px。**这才是「空一大片」的真因**。

本文件钉住:

  1. 两个页面各有一个「月历 | 周计划」分段切换器, 互为镜像;
  2. 周计划页有页宽豁免, 且**不破坏**基础 main 的阅读 measure;
  3. 周计划主区在宽屏是 7 列, 窄屏横向滚动;
  4. 课表是页内分区, 不是弹窗; 刻度范围是动态算的;
  5. 生成任务前有预览确认层。

不钉的部分: 切换器长什么样、点了之后怎么跳 —— 那是实现的自由。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "app" / "static" / "index.html"
APP_JS = ROOT / "app" / "static" / "app.js"
STYLES = ROOT / "app" / "static" / "styles.css"


def _strip_comments(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return re.sub(r"\s+", " ", css)


# ====== 1. 切换器存在 ======

def test_two_view_switchers_exist():
    """日历页与周计划页各有一个切换器。

    注意: 容器类名是 `view-switch`, 按钮是 `view-switch__btn` —— 计数要用
    容器的前缀 (带空格), 否则按钮也会被算进去。
    """
    html = INDEX.read_text(encoding="utf-8")
    assert html.count('class="view-switch ') == 2, \
        "应当有两个视图切换器容器 (日历页 + 周计划页)"
    assert html.count('class="view-switch__btn') == 4, \
        "每个切换器两个按钮, 共 4 个"
    for view in ("calendar", "week"):
        assert f'data-view="{view}"' in html, f"切换器缺少 data-view=\"{view}\""


def test_week_plan_id_is_kept():
    """id=btn-week-plan 必须留着 —— test_header_ui 钉着它。"""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="btn-week-plan"' in html
    assert 'id="btn-calendar-view"' in html


def test_week_view_has_a_head():
    """周计划页要有标题行, 否则深链进来不知道自己在哪。"""
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r'<section id="view-week".*?</section>', html, re.S)
    assert m, "缺少 #view-week"
    assert "view-head" in m.group(0), "周计划页缺少 .view-head"
    assert "周计划" in m.group(0), "周计划页缺少标题文案"


def test_switcher_is_not_inside_cal_mode():
    """视图切换器不许混进 .cal-mode 组。

    .cal-mode 的语义是「月历长什么样」(完成情况/用时热力图/贴纸册),
    三个选项都作用在同一个月历上。周计划是一整个页面, 混进去会让人
    以为它是月历的第四种画法。
    """
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r'<div class="cal-mode".*?</div>', html, re.S)
    assert m, "缺少 .cal-mode 组"
    assert "view-switch" not in m.group(0), "视图切换器不许塞进 .cal-mode 组"


# ====== 2. 页宽豁免 ======

def test_week_view_has_width_exemption():
    """周计划页单独放宽容器 —— 但基础 main 的阅读 measure 一点不动。"""
    css = _strip_comments(STYLES.read_text(encoding="utf-8"))
    assert re.search(r'body\[data-view="week"\]\s*main\s*\{[^}]*max-width', css), \
        "缺少 body[data-view=week] main 的宽度豁免"

    m = re.search(r"\bmain\s*\{[^}]*max-width:\s*(\d+)px", css)
    assert m, "main 缺少 max-width"
    w = int(m.group(1))
    assert 640 <= w <= 820, f"{w}px 不是合适的阅读 measure —— 基础规则不能动"


def test_body_view_attribute_is_set():
    """switchTab 必须写 body.dataset.view, 否则宽度豁免永远不生效。"""
    js = APP_JS.read_text(encoding="utf-8")
    assert "dataset.view" in js, "switchTab 未维护 body.dataset.view"
    assert "function syncViewSwitch" in js


# ====== 3. 7 列周视图 ======

def test_week_days_is_seven_columns():
    """宽屏 7 列 (周一~周日横向铺满)。"""
    css = _strip_comments(STYLES.read_text(encoding="utf-8"))
    m = re.search(r"\.week-days\s*\{([^}]*)\}", css)
    assert m, "缺少 .week-days"
    assert "repeat(7" in m.group(1), \
        f"宽屏 .week-days 不是 7 列: {m.group(1)[:120]}"


def test_narrow_screen_scrolls_horizontally():
    """窄屏 7 列放不下, 横向滚动 —— 挤成 1 列会失去「一周对比」。"""
    css = STYLES.read_text(encoding="utf-8")
    m = re.search(r"@media \(max-width: 767px\)[^{]*\{(.*?)\n\}", css, re.S)
    assert m, "缺少 (max-width: 767px) 媒体查询"
    assert ".week-days" in m.group(1), "窄屏没有为 .week-days 单独处理"
    assert "overflow-x" in m.group(1), "窄屏 .week-days 缺横向滚动"


def test_load_bar_exists():
    """每列要有负荷条, 「哪天最满」才不用逐个数。"""
    js = APP_JS.read_text(encoding="utf-8")
    assert "week-load-bar" in js, "缺负荷条"
    assert "limit_minutes" in js, "负荷条没按 limit_minutes 算比例"


# ====== 4. 课表时间轴分区 ======

def test_schedule_section_is_in_page_not_modal():
    """课表列表不再装在弹窗里 —— 改成周计划页里的独立分区。"""
    js = APP_JS.read_text(encoding="utf-8")
    assert "function renderScheduleSection" in js, "缺课表分区渲染函数"
    assert 'id="week-sched"' in js, "课表分区缺 id"
    # openScheduleManager 的名字要保留 (test_m5_week_plan 钉着)
    assert "function openScheduleManager" in js, "openScheduleManager 名字要保留"
    m = re.search(r"function openScheduleManager\s*\(\)\s*\{(.*?)\n\}", js, re.S)
    assert m, "找不到 openScheduleManager"
    assert "modal-content" not in m.group(1), \
        "openScheduleManager 不该再往弹窗里塞课表列表"


def test_time_range_is_computed_not_hardcoded():
    """刻度范围按本周实际课程动态算 —— 写死 15:00-21:00 的话,
    有早上 8 点的课就会画到框外。"""
    js = APP_JS.read_text(encoding="utf-8")
    assert "function scheduleTimeRange" in js, "缺刻度范围计算函数"
    body = re.search(r"function scheduleTimeRange.*?\n\}", js, re.S)
    assert body, "找不到 scheduleTimeRange"
    assert "start_time" in body.group(0), "刻度范围必须由课程时间算出来"
    assert "end_time" in body.group(0)
    assert "21:00" not in body.group(0), "刻度范围不能写死"


# ====== 5. 生成前预览 ======

def test_generate_has_preview_before_post():
    """点「生成本周任务」要先看预览, 确认后才写库。

    原来一点就直接 POST —— 模板写错了才发现就晚了。
    """
    js = APP_JS.read_text(encoding="utf-8")
    assert "function showGeneratePreview" in js, "缺预览层"
    m = re.search(r"function generateWeekTasks.*?\n\}", js, re.S)
    assert m, "找不到 generateWeekTasks"
    body = m.group(0)
    assert "showGeneratePreview" in body, "generateWeekTasks 必须先走预览"
    # POST 只能在预览回调里发生
    assert body.index("showGeneratePreview") < body.index("/week-plan/generate")
