# 周计划页改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让周计划（课外课程/课表 + 周计划项）重新可达 —— 日历页加「月历 | 周计划」分段切换器，周计划页宽屏铺满并改成 7 列周视图，课表改成页内 7 天时间轴分区，生成任务前加预览确认。

**Architecture:** 纯前端改造，后端零改动。现有 `GET /api/week-plan?date=` 已返回 `days` / `slots` / `plan_items` / `totals` / `conflicts`，够画周视图、时间轴与生成预览。三个新 DOM 块（视图切换器、7 列周视图、课表时间轴分区）全部由 `app.js` 渲染，样式加在 `styles.css`。

**Tech Stack:** FastAPI（只读，不改）+ 原生 JS（ES2020，无构建工具）+ 原生 CSS（CSS Grid）+ pytest（静态断言）+ jsdom（node harness）

**设计文档：** `docs/superpowers/specs/2026-11-02-week-plan-view-design.md`

---

## Global Constraints

这些是**全局红线**，每个任务都隐含遵守：

- **不改后端** —— 不新增接口、不加表、不写迁移、不动 `app/services/`、`app/routers/`、`app/models.py`
- **基础 `main { max-width: 720px }` 一个字不动** —— `tests/test_desktop_layout.py::test_reading_measure_is_capped` 要求 `main` 的 max-width 在 **640–820px** 之间
- **`id="btn-week-plan"` 必须保留在 `index.html`** —— `tests/test_header_ui.py::test_parent_only_page_entries_exist` 钉着
- **`.cal-mode` 组不许动** —— 那是「月历长什么样」（完成情况 / 用时热力图 / 贴纸册），与本次的页面级视图切换器无关；`tests/test_calendar_heatmap.py` 钉着
- **既有类名保留**：`.week-days` / `.week-day` / `.week-chips` / `.week-slot` / `.week-plan` / `.week-conflict` / `.manage-list` / `.manage-row` / `.weekday-check` —— `tests/test_m5_week_plan.py::test_styles_define_week_plan` 钉着
- **既有函数名保留**：`loadWeekPlan` / `renderWeekPlan` / `weekDayCard` / `generateWeekTasks` / `openScheduleManager` / `openScheduleSlotModal` / `openPlanItemManager` / `openPlanItemModal` —— `tests/test_m5_week_plan.py::test_frontend_has_week_page` 钉着
- **不许用原生 `window.confirm` / `alert` / `prompt`** —— 同上测试断言它们不在 `app.js` 里
- **配色沿用既有约定**：超载 = 琥珀（`--status-doing` / `--warn-soft`），冲突/逾期 = 红（`--status-overdue` / `--status-overdue-soft`），今天 = 粉框（`--accent-soft`）。课表 = 培训色（`--c-training` / `--c-training-soft`）。**红色与琥珀不能混用**
- **不引外部 CDN、不加 npm 运行时依赖、不加前端构建工具**
- **CSS 变量名用真实存在的**：不存在 `--st-late-c` 之外的别名随意造；超载/冲突分别用 `--status-doing` 与 `--status-overdue`
- **`node --check app/static/app.js` 必须通过**
- **中文文案**：界面语言是简体中文，Tab/按钮文案不含拉丁字母

---

## File Structure

| 文件 | 改什么 |
|---|---|
| `app/static/index.html` | 日历页的 `#btn-week-plan` 改成视图切换器；`#view-week` 加 `.view-head` + 第二个切换器 |
| `app/static/app.js` | `switchTab()` 维护 `body.dataset.view`；新增 `syncViewSwitch()` / `renderScheduleSection()` / `scheduleTimeRange()` / `showGeneratePreview()`；改 `renderWeekPlan()` / `weekDayCard()` / `generateWeekTasks()` / `openScheduleManager()` / `openScheduleSlotModal()` |
| `app/static/styles.css` | 新增 `.view-switch*` / `body[data-view="week"] main` 豁免 / `.week-load-bar` / `.week-sched*` / `.week-tl*` / `.gen-preview*`；改 `.week-days` 为 7 列 |
| `tests/test_week_view_switch.py` | **新建** —— 钉住切换器、宽度豁免、7 列、时间轴刻度动态、生成预览 |
| `tests/test_slot_modal_back.py` | **改** —— 「上一层」从弹窗列表变成页内分区 |
| `docs/07-双角色改造规范.md` | §1.3 家长端专属页表的「周计划」入口改写 |
| `docs/02-页面设计.md` | §5.4 周计划页重写 |
| `docs/08-重构约束与测试基线.md` | §4 补记本轮测试改动 |

---

## Task 1: 视图切换器 + 页宽豁免

**Files:**
- Modify: `app/static/index.html`（日历页 `.view-actions`；`#view-week` 段）
- Modify: `app/static/app.js`（`switchTab()`；文件末尾事件绑定区）
- Modify: `app/static/styles.css`（周计划样式区之后）
- Test: `tests/test_week_view_switch.py`（新建）

**Interfaces:**
- Consumes: 现有 `switchTab(name, opts)`、`TABS`（含 `'week'` / `'list'`）
- Produces:
  - `document.body.dataset.view` —— 当前 tab 名（`'board' | 'entry' | 'list' | 'week' | 'review' | 'settings'`）
  - `syncViewSwitch(tab)` —— 同步两个切换器的选中态
  - DOM：`.view-switch`（容器）> `.view-switch__btn[data-view]`（按钮）
  - DOM id：`#btn-week-plan`（日历页）、`#btn-calendar-view`（周计划页）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_week_view_switch.py`：

```python
"""周计划页改版: 视图切换器 + 页宽豁免 (2026-11-02).

用户反馈「前几天做的课外课程管理的功能, 当前顶部菜单已经见不到了」。
根因是两层:
  1. 2026-11-01 双角色改造把顶部 6 Tab 砍到 3 个, 周计划退成
     display:none 的深链落点, 只剩日历页右上角一个 ghost 灰色小字;
  2. styles.css 的 main { max-width: 720px } 把周计划页的内容列卡在
     688px —— 1280 视口下右边空 560px。这才是「空一大片」的真因。

本文件钉住:
  1. 两个页面各有一个「月历 | 周计划」分段切换器, 互为镜像;
  2. 周计划页有页宽豁免, 且**不破坏**基础 main 的阅读 measure;
  3. 切换器是页面级控件, 不许混进 .cal-mode 组 (那组是「月历长什么样」)。

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
    """日历页与周计划页各有一个切换器。"""
    html = INDEX.read_text(encoding="utf-8")
    assert html.count('class="view-switch') == 2, \
        "应当有两个视图切换器 (日历页 + 周计划页)"
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py -q
```

Expected: 8 failed（`test_two_view_switchers_exist` 等全红）

- [ ] **Step 3: 改 `index.html` —— 日历页切换器**

把 `#view-list` 段里的：

```html
        <div class="view-actions">
          <button type="button" class="ghost view-parent-only" id="btn-week-plan">周计划</button>
        </div>
```

换成：

```html
        <div class="view-actions">
          <!-- 视图切换器 (2026-11-02): 日历页与周计划页是「同一个地方
               的两种看法」, 所以用分段控件, 不是孤零零一个按钮。
               id=btn-week-plan 不能改 —— test_header_ui 钉着。 -->
          <div class="view-switch view-parent-only" id="btn-week-plan" role="group" aria-label="视图切换">
            <button type="button" class="view-switch__btn is-active" data-view="calendar" aria-pressed="true">月历</button>
            <button type="button" class="view-switch__btn" data-view="week" aria-pressed="false">周计划</button>
          </div>
        </div>
```

- [ ] **Step 4: 改 `index.html` —— 周计划页加标题行**

把 `#view-week` 段：

```html
    <section id="view-week" class="view" role="tabpanel">
      <div id="week-content"></div>
    </section>
```

换成：

```html
    <section id="view-week" class="view" role="tabpanel">
      <!-- 标题行 (2026-11-02): 之前这页直接是内容, 深链进来不知道
           在哪。现在有标题 + 同一个视图切换器, 能直接切回日历。 -->
      <div class="view-head">
        <h2 class="view-title">周计划</h2>
        <div class="view-actions">
          <div class="view-switch view-parent-only" id="btn-calendar-view" role="group" aria-label="视图切换">
            <button type="button" class="view-switch__btn" data-view="calendar" aria-pressed="false">月历</button>
            <button type="button" class="view-switch__btn is-active" data-view="week" aria-pressed="true">周计划</button>
          </div>
        </div>
      </div>
      <div id="week-content"></div>
    </section>
```

- [ ] **Step 5: 改 `app.js` —— 维护 `body.dataset.view` 与同步选中态**

在 `switchTab()` 里，`document.querySelectorAll('.view').forEach(...)` 那行**之后**插入：

```js
  // 当前视图记在 body 上, 供 CSS 用。CSS 不用 :has() —— 老浏览器
  // 不认, 而且这里只是切一个属性。
  // 2026-11-02: 周计划页靠 body[data-view="week"] 单独放宽容器
  // (7 列周视图 + 课表时间轴在 688px 里放不下)。
  document.body.dataset.view = tab;
  syncViewSwitch(tab);
```

在 `switchTab()` **之前**新增函数：

```js
// 两个页面各有一个「月历 | 周计划」切换器 (日历页 id=btn-week-plan,
// 周计划页 id=btn-calendar-view)。选中态必须两边同步 —— 从日历切到
// 周计划后, 周计划页顶上的那个也得显示「周计划」是亮的。
function syncViewSwitch(tab) {
  const target = tab === 'week' ? 'week' : 'calendar';
  document.querySelectorAll('.view-switch').forEach(g => {
    g.querySelectorAll('.view-switch__btn').forEach(b => {
      const on = b.dataset.view === target;
      b.classList.toggle('is-active', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  });
}
```

在文件末尾事件绑定区，**替换**原来那行：

```js
const weekPlanBtn = document.getElementById('btn-week-plan');
if (weekPlanBtn) weekPlanBtn.addEventListener('click', () => switchTab('week'));
```

为：

```js
// 视图切换: 日历页 <-> 周计划页。两个页面各有自己的切换器, 按钮靠
// data-view 声明自己是哪个视图, 所以这里用委托而不是逐个绑。
document.querySelectorAll('.view-switch__btn').forEach(b => {
  b.addEventListener('click', () => {
    switchTab(b.dataset.view === 'week' ? 'week' : 'list');
  });
});
```

- [ ] **Step 6: 改 `styles.css` —— 加切换器样式与页宽豁免**

在周计划样式区（`.week-flow-note` 那条之后）追加：

```css
/* ===== 视图切换器 (2026-11-02) =====
   日历页与周计划页各放一个, 内容相同、互为镜像。它表达的是
   「月历和周计划是同一个地方的不同看法」, 所以用分段控件, 而不是
   一个孤零零的按钮 —— 之前的 ghost 灰色小字看着像普通文字。 */
.view-switch {
  display: inline-flex;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--surface);
}
.view-switch__btn {
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: var(--fs-body-s);
  color: var(--text-2);
  padding: 6px var(--space-4);
  min-height: 36px;
  cursor: pointer;
}
.view-switch__btn:hover { background: var(--surface-2); }
.view-switch__btn.is-active {
  background: var(--accent);
  color: var(--accent-ink);
  font-weight: 600;
}

/* 周计划页单独放宽容器 (2026-11-02)。
   基础 main 的 max-width: 720px 是给「阅读型」页面准备的 —— 一行的
   字数不该太长。但周计划是仪表盘: 7 列周视图 + 课表时间轴在 688px
   里每格只剩 90px, 中文任务名会折成竖排。
   基础规则一个字不动 (tests/test_desktop_layout.py 要求 main 的
   max-width 在 640~820 之间), 只给这一页开豁免。
   1240px 下每格 (1240-32-6*12)/7 ≈ 156px, 与原来 4 列的 163px 同宽,
   当年那条「7 列每格只剩 130px」的反对意见自动消解。 */
body[data-view="week"] main { max-width: 1240px; }
```

- [ ] **Step 7: 跑测试确认通过**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py -q
```

Expected: `test_week_days_is_seven_columns` **仍失败**（7 列是 Task 2 的活），其余 7 条 PASS

- [ ] **Step 8: 跑既有测试确认没打破红线**

```bash
cd /home/xiaoduo/project/study-tracker
set -a && . ./.env >/dev/null 2>&1 && set +a
python3 -m pytest tests/test_header_ui.py tests/test_desktop_layout.py tests/test_calendar_heatmap.py tests/test_week_plan_flow.py tests/test_m5_week_plan.py -q
node --check app/static/app.js
```

Expected: 全 PASS（`test_header_ui` 的 `id="btn-week-plan"` 断言因 id 保留而仍然通过）

- [ ] **Step 9: 提交**

```bash
cd /home/xiaoduo/project/study-tracker
git add app/static/index.html app/static/app.js app/static/styles.css tests/test_week_view_switch.py
git commit -m "feat(week): 日历页加「月历 | 周计划」视图切换器 + 周计划页单独放宽容器

用户反馈「前几天做的课外课程管理的功能, 当前顶部菜单已经见不到了」。
根因是两层:
  1. 双角色改造把顶部 6 Tab 砍到 3 个, 周计划退成 display:none 的
     深链落点, 只剩日历页右上角一个 ghost 灰色小字;
  2. main { max-width: 720px } 把周计划页卡在 688px, 1280 视口下
     右边空 560px —— 这才是「空一大片」的真因。

改动:
- 日历页的 #btn-week-plan 改成「月历 | 周计划」分段切换器 (id 保留,
  test_header_ui 钉着); 周计划页顶上放同一个切换器, 两页互切。
- switchTab 维护 body.dataset.view; 新增 syncViewSwitch 同步两处选中态。
- body[data-view=\"week\"] main { max-width: 1240px } —— 基础 main 的
  720px 一个字不动 (test_desktop_layout 要求 640~820), 只给这一页豁免。
  1240 下 7 列每格 ≈156px, 与原来 4 列单格 163px 同宽。

不碰 .cal-mode 组 (那是「月历长什么样」, 与页面级切换器无关)。"
```

---

## Task 2: 7 列周视图 + 负荷条

**Files:**
- Modify: `app/static/styles.css`（`.week-days` 规则；窄屏媒体查询）
- Modify: `app/static/app.js`（`weekDayCard()`）
- Test: `tests/test_week_view_switch.py`（追加）

**Interfaces:**
- Consumes: `d.limit_minutes`、`d.load_minutes`（后端 `get_week_plan` 已返回）
- Produces: DOM `.week-load-bar` > `i`（宽度百分比 = `load/limit`）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_week_view_switch.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py -q
```

Expected: `test_narrow_screen_scrolls_horizontally` / `test_load_bar_exists` / `test_week_days_is_seven_columns` 失败

- [ ] **Step 3: 改 `styles.css` —— 7 列**

把 `.week-days` 整条规则（连同上面那段「不用 7 列」注释）替换为：

```css
/* 7 天卡: 桌面 7 列 (周一~周日横向铺满), 窄屏横向滚动。
   2026-11-02 改: 原来是 4 列, 注释里写着「不用 7 列是因为每格要塞
   课表 chips + 计划项 + 统计, 7 列时每格只剩 130px, 中文任务名会
   折成竖排」—— 那个反对意见本身没错, 但它成立的前提 (容器 688px)
   正是本次要解决的问题: 周计划页单独放宽到 1240px 之后, 7 列每格
   ≈156px, 与原来 4 列的单格 163px 同宽, 反对意见自动消解。 */
.week-days {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: var(--space-3);
}
```

- [ ] **Step 4: 改 `styles.css` —— 窄屏横向滚动**

在已有的 `@media (max-width: 480px)` 块**之后**新增一个块：

```css
/* 窄屏 (≤767px): 7 列在 375px 上每格只有 50px, 中文任务名会折成
   竖排。横向滚动保留「一周对比」的用处 —— 挤成 1 列就退回到改版
   前的 4 列网格, 反而更窄。 */
@media (max-width: 767px) {
  .week-days {
    grid-template-columns: repeat(7, minmax(150px, 1fr));
    overflow-x: auto;
    padding-bottom: var(--space-2);
  }
}
```

- [ ] **Step 5: 改 `styles.css` —— 负荷条**

在 `.week-day-head` 那组规则之后追加：

```css
/* 负荷条: 按 load / limit 的比例画一根细条, 让「哪天最满」不用
   逐列数字比。超载的列用琥珀色 —— 超载本来就是琥珀边, 保持一致。 */
.week-load-bar {
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--surface-2);
  overflow: hidden;
}
.week-load-bar i {
  display: block;
  height: 100%;
  background: var(--accent);
}
.week-day.overloaded .week-load-bar i { background: var(--status-doing); }
```

- [ ] **Step 6: 改 `app.js` —— `weekDayCard()` 加负荷条**

在 `weekDayCard()` 里，`const card = el(...)` 那一大段之后、`if (d.slots.length) {` **之前**插入：

```js
  // 负荷条: 按 load / limit 画比例。上限为 0 (没设) 时不画 —— 没有分母
  // 的比例条会骗人。
  if (d.limit_minutes > 0) {
    const pct = Math.min(100, Math.round(d.load_minutes / d.limit_minutes * 100));
    card.appendChild(el(`<div class="week-load-bar" title="${d.load_minutes} / ${d.limit_minutes} 分钟"><i style="width:${pct}%"></i></div>`));
  }
```

- [ ] **Step 7: 跑测试确认通过**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py tests/test_week_plan_flow.py tests/test_m5_week_plan.py -q
node --check app/static/app.js
```

Expected: 全 PASS

- [ ] **Step 8: 提交**

```bash
cd /home/xiaoduo/project/study-tracker
git add app/static/styles.css app/static/app.js tests/test_week_view_switch.py
git commit -m "feat(week): 周计划主区改 7 列等宽周视图 + 每列加负荷条

- .week-days 从 4 列改 7 列 (周一~周日横向铺满), 窄屏横向滚动。
  当年「7 列每格只剩 130px 会折成竖排」的反对意见, 在页宽豁免到
  1240px 之后不再成立 (每格 ≈156px, 与原 4 列单格 163px 同宽)。
- 每列加负荷条: 按 load / limit 画比例, 「哪天最满」不用逐列数字比。
  超载列用琥珀色, 与现有超载约定一致。"
```

---

## Task 3: 课表时间轴分区（页内，取代弹窗）

**Files:**
- Modify: `app/static/app.js`（新增 `scheduleTimeRange()` / `renderScheduleSection()`；改 `renderWeekPlan()`；改 `openScheduleManager()`）
- Modify: `app/static/styles.css`（新增 `.week-sched*` / `.week-tl*`）
- Test: `tests/test_week_view_switch.py`（追加）

**Interfaces:**
- Consumes: `w.slots`（`{id, project_id, project_name, weekday, start_time, end_time, minutes, note}`）、`w.days[].conflicts[].slot_ids`
- Produces:
  - `scheduleTimeRange(slots)` → `{start, end}`（分钟数，end > start）或 `null`（无课）
  - `renderScheduleSection(w)` → DOM `<section class="week-sched" id="week-sched">`
  - `openScheduleManager()` → 语义改为「定位到课表分区并高亮」（**不再开弹窗**）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_week_view_switch.py` 末尾追加：

```python
# ====== 课表时间轴分区 ======

def test_schedule_section_is_in_page_not_modal():
    """课表列表不再装在弹窗里 —— 改成周计划页里的独立分区。"""
    js = APP_JS.read_text(encoding="utf-8")
    assert "function renderScheduleSection" in js, "缺课表分区渲染函数"
    assert 'id="week-sched"' in js, "课表分区缺 id"
    # 旧的弹窗式列表渲染: 往 modal-content 里塞 .manage-list 的课表列表
    assert "function openScheduleManager" in js, "openScheduleManager 名字要保留"
    assert "openScheduleManager" in js
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py -q
```

Expected: 两个新测试失败

- [ ] **Step 3: 改 `app.js` —— 加刻度范围计算**

在 `function openScheduleManager()` **之前**插入：

```js
// 课表时间轴的刻度范围 (2026-11-02)。
// 按本周实际的最早开始到最晚结束, 向下/向上取整到小时。写死
// 15:00-21:00 的话, 有早上 8 点的课就会画到框外, 有晚上 9 点半的
// 课就会被切掉。没有课返回 null —— 不画空坐标系。
function scheduleTimeRange(slots) {
  if (!slots || !slots.length) return null;
  let min = 24 * 60;
  let max = 0;
  slots.forEach(s => {
    const a = hhmmToMinutes(s.start_time);
    const b = hhmmToMinutes(s.end_time);
    if (a < min) min = a;
    if (b > max) max = b;
  });
  const start = Math.floor(min / 60) * 60;
  let end = Math.ceil(max / 60) * 60;
  // 最小跨度 3 小时: 只有一节 30 分钟的课时, 轴塌成一格看不出早晚
  if (end - start < 180) end = start + 180;
  return { start: start, end: end };
}

function hhmmToMinutes(t) {
  const parts = String(t).split(':');
  const h = parseInt(parts[0], 10) || 0;
  const m = parseInt(parts[1], 10) || 0;
  return h * 60 + m;
}
```

- [ ] **Step 4: 改 `app.js` —— 加课表分区渲染**

在 `function openScheduleManager()` **之前**插入（紧跟上面的 `scheduleTimeRange`）：

```js
// 课表时间轴分区 (2026-11-02)。
//
// 之前课表是「弹窗里的平铺列表」: 要先点「管理」才看得见, 看到的是
// 一行行文字, 脑补不出「周三下午几点到几点有课」。现在它是周计划页
// 里的一个分区, 纵向时间轴 x 7 列, 一眼看到一周七天的分布。
//
// 撞车的两段错开叠放 (像 Google 日历), 并用红色标出 —— 红色在本项目
// 里是「冲突/逾期」专用, 超载是琥珀色, 两者不混。
function renderScheduleSection(w) {
  const sec = el(`<section class="week-sched" id="week-sched">
    <div class="week-sched-head">
      <h3 class="card-title">固定课表</h3>
      <button type="button" class="secondary" id="slot-add">＋ 新增时段</button>
    </div>
    <div class="week-sched-body"></div>
  </section>`);

  const body = sec.querySelector('.week-sched-body');
  const range = scheduleTimeRange(w.slots);
  if (!range) {
    body.appendChild(el('<p class="muted">这周还没有固定课表。加上「钢琴 周三 16:30-18:00」后, 周计划会把它算进当天负荷。</p>'));
    sec.querySelector('#slot-add').addEventListener('click', () => openScheduleSlotModal(null));
    return sec;
  }

  const rows = Math.round((range.end - range.start) / 60);
  const grid = el(`<div class="week-tl" style="grid-template-rows: 26px repeat(${rows}, 44px)"></div>`);

  // 表头: 周一 ~ 周日
  WEEKDAY_LABELS.forEach((label, i) => {
    grid.appendChild(el(`<div class="week-tl-hd${i === 2 ? ' is-today' : ''}" style="grid-column:${i + 2};grid-row:1">${label}</div>`));
  });
  // 刻度列 + 格子底
  for (let r = 0; r < rows; r++) {
    const hh = Math.floor(range.start / 60) + r;
    const label = (hh < 24 ? String(hh).padStart(2, '0') : '24') + ':00';
    grid.appendChild(el(`<div class="week-tl-tick" style="grid-column:1;grid-row:${r + 2}">${label}</div>`));
    for (let c = 0; c < 7; c++) {
      grid.appendChild(el(`<div class="week-tl-cell" style="grid-column:${c + 2};grid-row:${r + 2}"></div>`));
    }
  }

  // 同一天里撞车的两段: 后一段右移 30%, 视觉上叠着放
  const byDay = {};
  w.slots.forEach(s => {
    if (!byDay[s.weekday]) byDay[s.weekday] = [];
    byDay[s.weekday].push(s);
  });
  Object.keys(byDay).forEach(k => {
    const list = byDay[k].slice().sort((a, b) => (a.start_time < b.start_time ? -1 : 1));
    list.forEach((s, i) => {
      s.__off = 0;
      for (let j = 0; j < i; j++) {
        const a = hhmmToMinutes(list[j].start_time);
        const b = hhmmToMinutes(list[j].end_time);
        const c = hhmmToMinutes(s.start_time);
        const d = hhmmToMinutes(s.end_time);
        if (c < b && d > a) s.__off++;
      }
    });
  });

  // 撞车的 id: 后端已经在 days[].conflicts[].slot_ids 里给了
  const conflictIds = new Set();
  w.days.forEach(d => (d.conflicts || []).forEach(c => (c.slot_ids || []).forEach(id => conflictIds.add(id))));

  w.slots.forEach(s => {
    const a = hhmmToMinutes(s.start_time);
    const b = hhmmToMinutes(s.end_time);
    const r1 = Math.max(0, Math.floor((a - range.start) / 60));
    const r2 = Math.min(rows, Math.ceil((b - range.start) / 60));
    const off = (s.__off || 0) * 30;
    const cls = conflictIds.has(s.id) ? ' is-conflict' : '';
    const blk = el(`<button type="button" class="week-tl-blk${cls}" style="grid-column:${s.weekday + 2};grid-row:${r1 + 2}/${r2 + 2};margin-left:${off}%">
      <strong>${escapeAttr(s.project_name)}</strong>
      <small>${s.start_time}-${s.end_time}${s.note ? ' · ' + escapeAttr(s.note) : ''}</small>
    </button>`);
    blk.addEventListener('click', () => openScheduleSlotModal(s));
    grid.appendChild(blk);
  });

  body.appendChild(grid);
  sec.querySelector('#slot-add').addEventListener('click', () => openScheduleSlotModal(null));
  return sec;
}
```

- [ ] **Step 5: 改 `app.js` —— `renderWeekPlan()` 挂上分区**

在 `renderWeekPlan()` 里，`root.appendChild(days);` 这一行**之后**追加：

```js
  // 课表分区 (2026-11-02): 从弹窗里搬出来, 直接摆在页面下方。
  root.appendChild(renderScheduleSection(w));
```

- [ ] **Step 6: 改 `app.js` —— `openScheduleManager()` 改为定位分区**

把整个 `function openScheduleManager() { ... }`（含它内部往 `modal-content` 塞课表列表的全部代码）替换为：

```js
// 负荷卡里的「课表(分) · 管理」格子 (2026-11-02):
// 之前这里开的是弹窗 —— 课表列表装在弹窗里, 要点开才看得见。
// 现在列表是页面里的时间轴分区了, 再开弹窗等于把同一份内容弹两遍。
// 改成定位到分区并短暂描边, 家长点完就知道去哪儿改。
// 函数名保留 —— tests/test_m5_week_plan.py 钉着它。
function openScheduleManager() {
  const sec = document.getElementById('week-sched');
  if (!sec) return;
  sec.scrollIntoView({ behavior: 'smooth', block: 'center' });
  flashScheduleSection();
}

function flashScheduleSection() {
  const sec = document.getElementById('week-sched');
  if (!sec) return;
  sec.classList.add('is-flash');
  setTimeout(() => sec.classList.remove('is-flash'), 1600);
}
```

> **注意**：`openScheduleManager()` 旧实现里还有 `w.slots.forEach(...)` 的「改 / 删」按钮与 `delete` 事件，那部分整体删掉 —— 删除功能由单条编辑弹窗承担（Task 4 会把删除按钮加进弹窗）。

- [ ] **Step 7: 改 `styles.css` —— 加时间轴样式**

在 `.week-conflict` 那组规则之后追加：

```css
/* ---- 课表时间轴分区 (2026-11-02) ----
   课表从「弹窗里的平铺列表」搬到了页面里, 变成一张真正的课表纸:
   左边时间刻度, 7 天是 7 列, 课按时段画成色块。 */
.week-sched {
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
}
.week-sched.is-flash {
  outline: 3px solid var(--accent);
  outline-offset: 2px;
}
.week-sched-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.week-sched-head .card-title { margin: 0; }
.week-sched-body { overflow-x: auto; }

.week-tl {
  display: grid;
  grid-template-columns: 46px repeat(7, minmax(120px, 1fr));
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface);
  min-width: 720px;
}
.week-tl-hd {
  grid-row: 1;
  text-align: center;
  font-weight: 700;
  font-size: var(--fs-caption);
  color: var(--text-2);
  padding: 4px 0;
  border-bottom: 1px solid var(--border);
}
.week-tl-hd.is-today { color: var(--accent); }
.week-tl-tick {
  grid-column: 1;
  font-size: var(--fs-caption-sm);
  color: var(--text-3);
  text-align: right;
  padding-right: 6px;
  border-right: 1px solid var(--border);
}
.week-tl-cell {
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.week-tl-blk {
  border: none;
  border-left: 3px solid var(--c-training);
  border-radius: var(--radius-sm);
  background: var(--c-training-soft);
  color: #4B3272;
  font-family: inherit;
  text-align: left;
  padding: 3px 5px;
  margin: 2px 3px;
  overflow: hidden;
  cursor: pointer;
  z-index: 2;
}
.week-tl-blk strong { display: block; font-size: var(--fs-caption-sm); }
.week-tl-blk small { display: block; font-size: var(--fs-caption-sm); opacity: .75; }
/* 撞车: 红色 —— 本项目里红色是冲突/逾期专用, 超载是琥珀色。 */
.week-tl-blk.is-conflict {
  background: var(--status-overdue-soft);
  color: var(--status-overdue);
  border: 1px dashed var(--status-overdue);
  border-left: 3px solid var(--status-overdue);
}
.week-tl-blk:hover { filter: brightness(0.97); }
```

- [ ] **Step 8: 跑测试确认通过**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py tests/test_m5_week_plan.py -q
node --check app/static/app.js
```

Expected: 全 PASS

> `test_m5_week_plan.py::test_frontend_has_week_page` 钉着 `function openScheduleManager` —— 新实现仍是 `function openScheduleManager()`，通过。

- [ ] **Step 9: 提交**

```bash
cd /home/xiaoduo/project/study-tracker
git add app/static/app.js app/static/styles.css tests/test_week_view_switch.py
git commit -m "feat(week): 课表从弹窗列表搬成页内 7 天纵向时间轴分区

之前课表要点「管理」才看得见, 看到的是一行行文字, 脑补不出「周三
下午几点到几点有课」。现在它是周计划页里的独立分区: 左边时间刻度,
7 天是 7 列, 课按时段画成色块, 跟学校发的课表纸一个样子。

- 刻度范围**动态算**: 按本周实际的最早开始到最晚结束取整到小时,
  最小跨度 3 小时; 没有课显示空态, 不画空坐标系。写死 15:00-21:00
  的话, 早上 8 点的课会画到框外。
- 撞车的两段错开叠放 (像 Google 日历) 并标红。红色在本项目里是
  冲突/逾期专用, 超载是琥珀色 —— 两者不混。
- openScheduleManager() 语义从「开弹窗」改成「定位到分区并描边」。
  函数名保留 (test_m5_week_plan 钉着)。"
```

---

## Task 4: 单条编辑弹窗（「返回」语义调整 + 删除按钮）

**Files:**
- Modify: `app/static/app.js`（`openScheduleSlotModal()`）
- Modify: `tests/test_slot_modal_back.py`（改写）
- Test: `tests/test_slot_modal_back.py`

**Interfaces:**
- Consumes: `openScheduleSlotModal(slot)` —— `slot` 为 `null` 表示新增
- Produces: 弹窗内容含「删除」按钮（仅编辑模式）；次要键文案仍是「返回」

- [ ] **Step 1: 改写 `tests/test_slot_modal_back.py` 的断言**

课表列表不再是弹窗了，「上一层」变成页内分区。把该文件里断言
「点『返回』退回课表列表层（标题回到『固定课表』）」的部分改成
「点『返回』关闭弹窗并高亮页内课表分区」。

在 `tests/test_slot_modal_back.py` 末尾**追加**：

```python
# ====== 2026-11-02: 课表列表搬进页面后, 「上一层」的含义变了 ======

def test_cancel_text_is_still_return(index_html, app_js):
    """次要键的文案仍然是「返回」, 不是「取消」。

    「取消」让人以为点它等于放弃这次新增; 「返回」只表示退回上一层。
    这个区分跟课表列表在不在弹窗里无关, 不能因为改了版式就丢掉。
    """
    assert 'cancelText: \'返回\'' in app_js, "次要键文案必须是「返回」"


def test_return_closes_modal_and_flashes_section(app_js):
    """点「返回」= 关闭弹窗 + 高亮页内课表分区。

    2026-11-02 之前课表列表装在弹窗里, 「返回」要退回那个列表层。
    现在列表是页面里的分区了, 所以「返回」= 关窗 + 闪一下分区,
    让家长看见自己退回到了哪里。
    """
    m = re.search(r"function openScheduleSlotModal.*?\n\}", app_js, re.S)
    assert m, "找不到 openScheduleSlotModal"
    body = m.group(0)
    assert "cancelText: '返回'" in body, "次要键文案必须是「返回」"
    assert "flashScheduleSection" in body, "「返回」应高亮页内课表分区"
    assert "openScheduleManager()" not in body, \
        "「返回」不该再退回弹窗列表层 (那个层已经没有了)"


def test_slot_modal_has_delete_button(app_js):
    """删除功能随列表一起搬进弹窗 —— 列表里原来的「删」按钮没了。"""
    m = re.search(r"function openScheduleSlotModal.*?\n\}", app_js, re.S)
    assert m
    assert "删除" in m.group(0), "编辑弹窗里要有删除按钮"
```

> 同时把原文件里**依赖弹窗列表层**的 jsdom 用例改掉：
> - `click(doc.getElementById('week-manage-slots'))` 之后断言弹窗打开（`.manage-list` 存在）→ 改成断言弹窗**没有**打开、`#week-sched` 分区存在
> - `click(doc.getElementById('slot-add'))` 依赖列表层 → 改成直接调 `openScheduleSlotModal(null)`
> - 断言 `modal-title` 回到「固定课表」→ 改成断言 `modal-title` 仍是「新增上课时段」且弹窗已关闭

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_slot_modal_back.py -q
```

Expected: 新增的 3 条失败

- [ ] **Step 3: 改 `app.js` —— 弹窗次要键改语义**

在 `openScheduleSlotModal()` 里，把：

```js
    cancelText: '返回',
    onCancel: () => openScheduleManager(),
```

换成：

```js
    cancelText: '返回',
    // 2026-11-02: 课表列表从弹窗搬到了页内分区, 所以「上一层」不再是
    // 一个弹窗层, 而是页面里的课表分区。「返回」= 关窗 + 闪一下分区,
    // 让家长知道自己退回到了哪里, 而不是以为整件事被撤销了。
    onCancel: () => {
      closeModal();
      flashScheduleSection();
    },
```

- [ ] **Step 4: 改 `app.js` —— 弹窗里加删除按钮**

在 `openScheduleSlotModal()` 的 `content.innerHTML = ...` 模板里，`</div>`（`.form-grid` 结束）**之后**插入：

```html
    ${slot ? '<div class="row"><button type="button" class="danger" id="slot-del">删除这个时段</button></div>' : ''}
```

并在 `initProjectPickers(content);` **之后**插入：

```js
  const del = content.querySelector('#slot-del');
  if (del) del.addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: '删除上课时段',
      lead: `删除「${slot.project_name} ${WEEKDAY_LABELS[slot.weekday]} ${slot.start_time}-${slot.end_time}」?`,
      okText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await api('DELETE', `/schedule-slots/${slot.id}`);
      toast('已删除课表时段');
      closeModal();
      await loadWeekPlan();
    } catch (e) {
      toast('删除失败: ' + e.message);
    }
  });
```

> `confirmDialog` 会打开第二个弹窗，删除成功后要 `closeModal()` 关掉它 —— `confirmDialog` 内部在 `onOk` 里调 `closeModal(true)`，但删除失败时弹窗要留着，所以这里显式关一次。

- [ ] **Step 5: 跑测试确认通过**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_slot_modal_back.py -q
node --check app/static/app.js
```

Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
cd /home/xiaoduo/project/study-tracker
git add app/static/app.js tests/test_slot_modal_back.py
git commit -m "feat(week): 单条课表编辑弹窗适配页内分区 (返回=关窗+高亮, 弹窗内补删除)

课表列表搬进页面后, 「上一层」不再是弹窗层, 而是页面里的课表分区:
- 次要键仍是「返回」(不是「取消」—— 那个区分跟版式无关, 不能丢),
  点它 = 关闭弹窗 + 高亮页内分区, 让家长知道自己退回到了哪里;
- 列表里原来的「删」按钮没有了, 删除补进编辑弹窗;
- 冲突提示仍留在弹窗里可改, 不退化成浏览器原生弹窗。"
```

---

## Task 5: 生成前预览确认层

**Files:**
- Modify: `app/static/app.js`（新增 `showGeneratePreview()`；改 `generateWeekTasks()`）
- Modify: `app/static/styles.css`（新增 `.gen-preview*`）
- Test: `tests/test_week_view_switch.py`（追加）

**Interfaces:**
- Consumes: `w.days[].plans[].generated`、`w.days[].date`、`w.today`
- Produces: `showGeneratePreview(w, onConfirm)` → `boolean`（无待生成项时返回 `false` 且不弹层）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_week_view_switch.py` 末尾追加：

```python
# ====== 生成前预览 ======

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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py -q
```

Expected: `test_generate_has_preview_before_post` 失败

- [ ] **Step 3: 改 `app.js` —— 加预览层**

在 `function generateWeekTasks(w) {` **之前**插入：

```js
// 「生成本周任务」之前先给一张预览 (2026-11-02)。
// 原来一点就直接写库: 模板写错了、周几选错了, 都是任务已经进池子
// 之后才发现, 清理起来比看一眼麻烦得多。
// 待生成的口径与后端 generate_week 一致: 只算今天及以后, 且不重复
// 已经生成过的 (days[].plans[].generated 是后端算好的)。
function showGeneratePreview(w, onConfirm) {
  const items = [];
  w.days.forEach(d => {
    if (d.date < w.today) return;
    d.plans.forEach(p => {
      if (p.generated) return;
      items.push({
        date: d.date,
        name: p.task_name,
        minutes: p.est_minutes || 0,
        kind: p.subject || p.project_name || '',
      });
    });
  });
  if (!items.length) return false;

  const total = items.reduce((s, it) => s + it.minutes, 0);
  const byDay = {};
  items.forEach(it => {
    if (!byDay[it.date]) byDay[it.date] = [];
    byDay[it.date].push(it);
  });

  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = `将生成 ${items.length} 项任务`;
  const content = document.getElementById('modal-content');
  content.innerHTML = `<p class="muted">合计 ${total} 分钟 · 只生成今天及以后的日子</p>
    <div class="gen-preview">${Object.keys(byDay).sort().map(ds => `
      <div class="gen-preview-day">
        <strong>${md(ds)}</strong>
        ${byDay[ds].map(it => `
          <div class="gen-preview-item">
            <span>${escapeAttr(it.name)}</span>
            <span class="muted">${it.minutes} 分${it.kind ? ' · ' + escapeAttr(it.kind) : ''}</span>
          </div>`).join('')}
      </div>`).join('')}
    </div>`;
  m.classList.remove('hidden');
  m.classList.remove('confirm-modal');
  setModalActions({
    mode: 'confirm',
    okText: '确认生成',
    onOk: () => {
      closeModal();
      onConfirm();
    },
  });
  return true;
}
```

- [ ] **Step 4: 改 `app.js` —— `generateWeekTasks()` 走预览**

把整个 `function generateWeekTasks(w) { ... }` 替换为：

```js
async function generateWeekTasks(w) {
  // 先给预览, 家长确认了才写库。没有待生成项时直接 toast, 不弹空层。
  const ok = showGeneratePreview(w, async () => {
    const btn = document.getElementById('week-generate');
    if (btn) btn.disabled = true;
    try {
      const anchor = _weekAnchor || w.today;
      const r = await api('POST', `/week-plan/generate?date=${anchor}`);
      if (r.created_count) {
        toast(`已生成 ${r.created_count} 项任务${r.skipped_count ? `, 跳过 ${r.skipped_count} 项重复` : ''}`);
      } else if (r.skipped_count) {
        toast('这些任务都已经在任务池里了');
      } else {
        toast('还没有周计划项, 先在第 1 步添加模板');
      }
      await loadWeekPlan();
    } catch (e) {
      toast('生成失败: ' + e.message);
      if (btn) btn.disabled = false;
    }
  });
  if (!ok) {
    toast('没有待生成的任务');
  }
}
```

- [ ] **Step 5: 改 `styles.css` —— 预览层样式**

在 `.week-conflict` 那组规则之后追加：

```css
/* ---- 生成预览 (2026-11-02) ----
   「生成本周任务」之前给家长看一眼要写什么。 */
.gen-preview {
  max-height: 46vh;
  overflow-y: auto;
  margin-top: var(--space-2);
}
.gen-preview-day {
  margin-bottom: var(--space-2);
}
.gen-preview-day strong {
  display: block;
  font-size: var(--fs-caption);
  color: var(--text-2);
  margin-bottom: 2px;
}
.gen-preview-item {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 3px 0;
  border-bottom: 1px dashed var(--border);
  font-size: var(--fs-body-s);
}
```

- [ ] **Step 6: 跑测试确认通过**

```bash
cd /home/xiaoduo/project/study-tracker
python3 -m pytest tests/test_week_view_switch.py tests/test_week_plan_flow.py -q
node --check app/static/app.js
```

Expected: 全 PASS

- [ ] **Step 7: 提交**

```bash
cd /home/xiaoduo/project/study-tracker
git add app/static/app.js app/static/styles.css tests/test_week_view_switch.py
git commit -m "feat(week): 「生成本周任务」前加预览确认层

原来一点就直接 POST —— 模板写错了、周几选错了, 都是任务已经进池子
之后才发现, 清理比看一眼麻烦得多。现在先列出将要生成的条目 (条数 /
合计分钟 / 按天分组), 确认后才写库。

待生成口径与后端 generate_week 一致: 只算今天及以后, 且不重复已经
生成过的 (days[].plans[].generated 是后端算好的)。没有待生成项时
直接 toast, 不弹空层。"
```

---

## Task 6: 文档同步 + 全量回归

**Files:**
- Modify: `docs/07-双角色改造规范.md`（§1.3）
- Modify: `docs/02-页面设计.md`（§5.4）
- Modify: `docs/08-重构约束与测试基线.md`（§4）
- Modify: `README.md`（功能清单，若有周计划入口描述）

**Interfaces:**
- Consumes: 本次实施的实际结果
- Produces: 文档与实现一致

- [ ] **Step 1: 改 `docs/07-双角色改造规范.md` §1.3**

把家长端专属页表里的「周计划」那一行：

```markdown
| 周计划 | 日历页右上「周计划」按钮 | `#/week` |
```

改成：

```markdown
| 周计划 | 日历页右上「月历 \| 周计划」分段切换器（周计划页顶上也有同一个，可切回） | `#/week` |
```

- [ ] **Step 2: 改 `docs/02-页面设计.md` §5.4**

把 §5.4 整节替换为：

```markdown
### 5.4 周计划页（`#/week`，家长端专属）

入口是日历页标题行右上的「月历 | 周计划」分段切换器，周计划页顶上也有
同一个切换器（选中「月历」切回日历）。两个页面互为镜像。

页面结构：

1. **周切换条** —— ‹ 上周 / 本周日期区间 + 每天上限 / 下周 ›，翻到别的周时出现「回到本周」
2. **本周负荷卡** —— 4 格：总负荷 / 课表(分)·管理 / 超载天数 / 冲突天数。「课表」那一格是按钮，点它定位到下方的课表分区
3. **两步流程** —— ① 周计划项（模板）→ ② 生成本周任务。第②步点下去先出预览确认层，确认后才写库
4. **7 列周视图** —— 周一到周日横向铺满（窄屏横向滚动），每列：日期 + 负荷 + 负荷条 + 课表 chip + 计划项 chip + 作业统计。今天是粉框，超载是琥珀边 + 角标
5. **固定课表分区** —— 纵向时间轴 × 7 列，刻度范围按本周实际课程动态计算，撞车的两段错开叠放并标红

页宽：周计划页单独放宽到 1240px（其余页面仍是 720px 的阅读 measure）。
```

- [ ] **Step 3: 改 `docs/08-重构约束与测试基线.md` §4**

在 §4 的表格后面追加一节：

```markdown
### 4.2 周计划页改版那一轮改过的测试文件（2026-11-02）

| 文件 | 改了什么 | 为什么必须改 |
|---|---|---|
| `tests/test_slot_modal_back.py` | 「返回上一层」的断言从「退回弹窗列表层」改为「关闭弹窗 + 高亮页内课表分区」 | 课表列表从弹窗搬到了页内分区，上一层不再是弹窗层 |
| `tests/test_week_view_switch.py` | **新建** | 钉住视图切换器、页宽豁免、7 列周视图、时间轴刻度动态计算、生成预览 |

**原则不变**：只有当旧断言与新设计**直接冲突**时才改；其余一律保留。
`test_week_plan_flow` / `test_desktop_layout` / `test_calendar_heatmap` /
`test_header_ui` / `test_m5_week_plan` 这一轮一个字没动。

**红线一条没松**：
- 基础 `main { max-width: 720px }` 不动 —— 页宽豁免是 `body[data-view="week"] main`，
  只作用于周计划页，且写在基础规则之后（正则仍先命中基础规则）
- `.cal-mode` 组不动 —— 那是「月历长什么样」，与页面级视图切换器无关
- `id="btn-week-plan"` 保留 —— `test_header_ui` 钉着
- 后端零改动：不新增接口、不加表、不写迁移
```

- [ ] **Step 4: 跑全量测试**

```bash
cd /home/xiaoduo/project/study-tracker
set -a && . ./.env >/dev/null 2>&1 && set +a
python3 -m pytest tests/ -q
npm run test:js
node --check app/static/app.js
node --check app/static/charts.js
node --check app/static/dual.js
```

Expected: pytest 全绿；`npm run test:js` 全绿；三个 `node --check` 无输出（通过）

- [ ] **Step 5: 浏览器实测**

```bash
# 起服务 (已在跑则跳过)
cd /home/xiaoduo/project/study-tracker && pm2 restart study-tracker
```

打开 `http://127.0.0.1:8876/#/list`，逐条核对设计文档 §九 的验收标准 A1–A12。

- [ ] **Step 6: 提交**

```bash
cd /home/xiaoduo/project/study-tracker
git add docs/07-双角色改造规范.md docs/02-页面设计.md docs/08-重构约束与测试基线.md README.md
git commit -m "docs(week): 同步周计划页改版的设计契约

- 07 §1.3: 家长端专属页表里「周计划」的入口改成分段切换器 (两页互切)
- 02 §5.4: 重写周计划页页面设计 (7 列周视图 + 课表时间轴 + 生成预览)
- 08 §4.2: 补记本轮测试改动与「红线一条没松」的清单"
```

---

## Self-Review

**Spec coverage**

| 设计文档章节 | 对应任务 |
|---|---|
| §3.2 视图切换器 | Task 1 |
| §3.3 宽度豁免 | Task 1 |
| §3.4 7 列周视图 | Task 2 |
| §3.5 课表时间轴分区 | Task 3 + Task 4 |
| §3.6 生成前预览 | Task 5 |
| §5 错误处理 | Task 3/4/5（各任务内含） |
| §6 测试 | Task 1–5（各任务写自己的测试）+ Task 6（全量） |
| §7 文档同步 | Task 6 |
| §9 验收标准 A1–A13 | Task 6 Step 5 实测 |

**Type consistency**
- `scheduleTimeRange(slots)` → `{start: number, end: number}`（分钟）或 `null`；Task 3 定义、Task 3 内使用 ✓
- `renderScheduleSection(w)` → `HTMLElement`；Task 3 定义、Task 3 Step 5 使用 ✓
- `showGeneratePreview(w, onConfirm)` → `boolean`；Task 5 定义、Task 5 Step 4 使用 ✓
- `flashScheduleSection()` → `void`；Task 3 定义、Task 4 使用 ✓
- `syncViewSwitch(tab)` → `void`；Task 1 定义、Task 1 使用 ✓

**Scope**：全部落在前端（`index.html` / `app.js` / `styles.css`）+ 测试 + 文档。后端不动。✓
