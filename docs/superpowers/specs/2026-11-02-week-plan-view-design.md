# 周计划页改版设计（入口恢复 + 7 列周视图 + 课表时间轴）

日期：2026-11-02
状态：已与用户确认（视觉方案经浏览器线框图比对选定），待实现
配套：`docs/07-双角色改造规范.md`（双角色契约）、`docs/08-重构约束与测试基线.md`（红线清单）、`docs/02-页面设计.md` §5.4

---

## 一、背景与根因

### 1.1 用户反馈的现象

> 「前几天做了课外课程管理的功能，但是当前顶部菜单已经见不到了」

用户指的是**周计划**功能（`#/week`），它管两件事：

1. **固定课表**（`schedule_slots`）—— 课外班 / 兴趣班的星期 + 时间段
2. **周计划项**（`plan_items`）—— 每周重复做的任务模板，一键展开成当周作业

### 1.2 现象的真实成因（两层）

**第一层：导航精简。** 2026-11-01 双角色改造把顶部导航从 6 个 Tab 砍到 3 个
（今日 / 日历 / 复盘），「登记 / 周计划 / 设置」退成 `display:none` 的**深链落点**，
只在页面内留入口。按 `docs/07-双角色改造规范.md` §1.3 的设计，周计划入口是
「日历页右上『周计划』按钮」。

**第二层（此前没人注意到的真因）：内容容器被卡死。**
`app/static/styles.css:392` 有 `main { max-width: 720px }` —— 整个应用的内容列
限宽 720px。实测（1280×900 视口，Chrome）：

| 位置 | 实测值 |
|---|---|
| 视口宽度 | 1280px |
| `main` 内容列 | 688px（720 − 32 内边距） |
| `.week-days`（当前 4 列）每格 | 163px |
| 屏幕右侧空白 | 约 560px |

所以「宽屏右边空一大片」**不是布局没排好，是容器只用了屏幕一半**。

**连带后果：7 列周视图曾被否。** `styles.css` 里留着一段注释：

> 不用 7 列是因为每格要塞课表 chips + 计划项 + 统计, 7 列时每格只剩 130px,
> 中文任务名会折成竖排。

在 688px 里塞 7 列确实只有 ~92px，中文任务名会折成竖排 —— 这个反对意见是
**对的**，但它成立的前提（容器 688px）正是本次要解决的问题。

### 1.3 目标

| # | 目标 | 判定标准 |
|---|---|---|
| G1 | 周计划从日历页**一步可达**，不再需要「认出日历页右上角那个灰色小字」 | 日历页标题行有显式的「月历 \| 周计划」分段切换器 |
| G2 | 周计划页在宽屏上**用满宽度** | 1280px 视口下内容列从 688px 扩到 ~1240px |
| G3 | 一周七天的分布**一眼看完** | 主区为 7 列等宽周视图，每列宽度 ≥ 今天的 4 列单格宽度 |
| G4 | 课表**看得见时间轴**，不用点进弹窗 | 页内独立分区，纵向时间轴 × 7 列 |
| G5 | 生成任务**先看后写** | 点「生成本周任务」先出预览确认层，确认后才写库 |

---

## 二、已确认的产品决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | 入口 = 日历页**分段切换器**「月历 \| 周计划」 | 不是实心按钮，也不是塞进 `.cal-mode` 组。切换器表达「同一处的两种看法」，且周计划页顶上有同一个切换器可切回来 |
| D2 | 周计划主区 = **7 列等宽周视图** | 周一至周日横向铺满，超载列淡红底、今天列描蓝 |
| D3 | 课表 = 页内**独立分区**，纵向时间轴 × 7 列 | 不再用弹窗装课表列表 |
| D4 | 课表**单条编辑仍走弹窗** | 列表在页内；改一条课要填 5 个字段，弹窗与项目其它表单一致 |
| D5 | 生成任务前加**预览确认层** | 列出今天及以后将要生成的条目，确认后才 POST |
| D6 | 周计划页**单独放宽容器**到 1240px | 只给这一页豁免，其余页面仍是 720px |

**明确不做**（本轮范围外）：超载 / 冲突的**主动提示**（如「把哪一项挪到周四」这类
建议文案）。课表时间轴上撞车的两段仍会标红 —— 那是时间轴视图的固有信息，
不是新增的提示功能。

---

## 三、架构

### 3.1 页面结构

```
index.html
├── <main>                                   ← max-width: 720px（基础规则不动）
│   ├── #view-board    （今日）
│   ├── #view-entry    （登记，深链落点）
│   ├── #view-list     （日历）
│   │   └── .view-head
│   │       ├── h2 作业日历
│   │       └── #btn-week-plan  ← 视图切换器（含 id="btn-week-plan"）
│   ├── #view-week     （周计划）
│   │   ├── .view-head                        ← 本次新增
│   │   │   ├── h2 周计划
│   │   │   └── #btn-calendar-view ← 视图切换器
│   │   └── #week-content                     ← 由 app.js 渲染
│   ├── #view-review   （复盘）
│   └── #view-settings （设置，深链落点）
```

渲染分层（`app.js`）：

```
loadWeekPlan()                      GET /api/week-plan?date=
└── renderWeekPlan(root, w)
    ├── renderWeekHead()            标题 + 视图切换器 + 周切换条 + 「回到本周」
    ├── renderWeekSummary(w)        本周负荷 4 格
    ├── renderWeekFlow(w)           两步流程 ① 周计划项 → ② 生成本周任务
    ├── renderWeekDays(w)           7 列周视图（每列 = weekDayCard）
    └── renderScheduleSection(w)    课表时间轴分区（新）
        └── openScheduleSlotModal() 单条编辑弹窗（保留）
```

### 3.2 视图切换器

**两个页面各一个，内容相同、互为镜像：**

```html
<!-- 日历页 -->
<div class="view-switch view-parent-only" id="btn-week-plan" role="group" aria-label="视图切换">
  <button type="button" class="view-switch__btn active" data-view="calendar">月历</button>
  <button type="button" class="view-switch__btn" data-view="week">周计划</button>
</div>

<!-- 周计划页 -->
<div class="view-switch view-parent-only" id="btn-calendar-view" role="group" aria-label="视图切换">
  <button type="button" class="view-switch__btn" data-view="calendar">月历</button>
  <button type="button" class="view-switch__btn active" data-view="week">周计划</button>
</div>
```

**硬约束**：`id="btn-week-plan"` 必须保留在 `index.html` 里 ——
`tests/test_header_ui.py::test_parent_only_page_entries_exist` 钉着这个 id。

| 项 | 规则 |
|---|---|
| 点击「周计划」 | `switchTab('week')` → URL 变 `#/week` |
| 点击「月历」 | `switchTab('list')` → URL 变 `#/list` |
| 选中态同步 | `syncViewSwitch()` 同时更新两个容器（`aria-pressed` + `.active`） |
| 角色隔离 | 两个容器都带 `.view-parent-only`，孩子端隐藏 |
| 与 `.cal-mode` 的关系 | **互不相干**。`.cal-mode`（完成情况 / 用时热力图 / 贴纸册）是月历内部的显示方式；视图切换器是页面级。两者名字不能混 |

### 3.3 宽度豁免

```css
/* 基础规则不动 —— tests/test_desktop_layout.py::test_reading_measure_is_capped
   要求 main 的 max-width 在 640~820px 之间 */
main { max-width: 720px; ... }

/* 本次新增：只给周计划页放宽。放在基础规则之后，
   基础 main 规则仍是文件里第一个 max-width 命中，测试照样过。 */
body[data-view="week"] main { max-width: 1240px; }
```

`data-view` 由 `switchTab()` 统一维护（`document.body.dataset.view = tab`），
不复位、无副作用。

**为什么 1240px**：`(1240 − 32 − 6×12) ÷ 7 ≈ 156px/列`，
与今天的 4 列单格 163px 基本同宽 —— §1.2 那条「130px 太窄」的反对意见自动消解。

**其它页面的阅读 measure 完全不变**（720px）。

### 3.4 7 列周视图

| 断点 | 列数 | 行为 |
|---|---|---|
| ≥ 768px | `repeat(7, minmax(0, 1fr))` | 等宽铺满 |
| ≤ 767px | `repeat(7, minmax(140px, 1fr))` + `overflow-x: auto` | 横向滚动 |

**沿用现有类名**（`tests/test_m5_week_plan.py` 钉着）：
`.week-days` / `.week-day` / `.week-chips` / `.week-slot` / `.week-plan` / `.week-conflict`。

每列（`weekDayCard`）内部结构在现有基础上调整：

```
┌ 周三            11/4 ┐
│ 130 分  [超载]        │  ← 负荷 + 超载徽标
│ ▓▓▓▓▓▓▓▓▓▓ 负荷条      │  ← 新增：按 load/limit 比例
│ ┃国际象棋 16:00-18:00  │  ← 课表 chip（时间换行显示）
│ ┃每日阅读 10分         │  ← 周计划项 chip
│ 作业 0 项 · 待生成 10 分│
└──────────────────────┘
```

- 今日：整列 `box-shadow: inset 0 0 0 2px var(--accent)`（描蓝；`--accent` 在家长端 = `#1D4E7C`）
- 超载：沿用**现有**约定 `.week-day.overloaded { border-color: var(--status-doing) }`
  —— 超载是**橙色**（`--status-doing` / `--warn-soft`），**红色留给逾期与冲突**
  （`--status-overdue` = `#C2321F`），两者不能混
- 课表 chip 内时间换行（`国际象棋` / `16:00-18:00` 两行），避免窄列里中文折成竖排

### 3.5 课表时间轴分区

**位置**：周计划页主区（7 列周视图）**下方**，独立分区，不再是弹窗。

```
固定课表                      ＋ 新增时段
 15:00 ┼───┼───┼────────┼───┼───┼───┤
 16:00 ┼───┼───┼███████████┼░░░┼───┼───┤
 17:00 ┼───┼───┼███████████┼░░░┼───┼───┤
 18:00 ┼───┼───┼─────────┼───┼───┼───┤
 19:00 ┼───┼───┼─────────┼───┼───┼───┤
 20:00 ┼───┼───┼─────────┼───┼───┼███┼───┤
                            周三  周四    周六
```

**刻度范围动态计算**（不写死）：

```
若本周有课：
  start = floor_to_hour(min(slot.start_time))
  end   = ceil_to_hour(max(slot.end_time))
  span  = max(end - start, 3 小时)     ← 最小跨度，防止只有一节 30 分钟的课时轴塌成一格
若本周无课：
  显示空态「这周还没有固定课表」+ [＋ 新增时段] 按钮，不画空坐标系
```

**渲染实现**：CSS Grid，`grid-template-rows: 26px repeat(N, 44px)`，
课块作为网格项直接定位：

| 属性 | 取值 |
|---|---|
| `grid-column` | 星期索引 + 2（跳过刻度列） |
| `grid-row` | 由 `start_time` / `end_time` 映射到小时行 |
| 撞车的两段 | 后一段 `margin-left: 30%`，视觉上错开叠放 |
| 撞车配色 | `--status-overdue`（`#C2321F`）/ `--status-overdue-soft`（`#FBE7E3`）+ 虚线描边 |
| 正常配色 | 培训色 `--c-training` / `--c-training-soft`（沿用 `.week-slot` 现有配色） |

**交互**：

| 操作 | 结果 |
|---|---|
| 点课块 | 打开**单条**编辑弹窗（`openScheduleSlotModal`，与现状一致） |
| 点「＋ 新增时段」 | 同一弹窗的新增模式 |
| 点负荷卡的「课表(分)·管理」格子 | **滚动到本分区并短暂高亮**，不再开弹窗 |
| 改完保存 | `PATCH /api/schedule-slots/{id}` → 重新拉数据 → 分区重绘 |

`#week-manage-slots` 这个 id 与它在负荷格里的位置**不变**
（`tests/test_week_plan_flow.py` 钉着：`body.count('id="week-manage-slots"') == 1`）。

### 3.6 生成前预览

```
点「生成本周任务」
└── 收集待生成条目
    └── for day in w.days where day.date >= w.today
        └── for p in day.plans where !p.generated
            └── { 日期, 任务名, 科目/项目, 预计分钟 }
    ├── 条目为空 → 直接 toast「没有待生成」，不弹层（与现状一致）
    └── 条目非空 → 弹预览确认层
        ├── 标题「将生成 N 项任务」
        ├── 副标题「合计 M 分钟 · 只生成今天及以后」
        ├── 按天分组列出
        └── [取消] [确认生成]
            └── 确认 → POST /api/week-plan/generate?date= → toast → 重新加载
```

数据来源：`w.days[].plans[].generated`（后端已按「当天是否已有同名任务」算好）。

### 3.7 后端

**零改动。** 现有接口已满足全部需求：

| 用途 | 接口 | 状态 |
|---|---|---|
| 周计划整周数据 | `GET /api/week-plan?date=` | 已有（含 `days` / `slots` / `plan_items` / `totals` / `conflicts`） |
| 课表增删改 | `POST` / `PATCH` / `DELETE /api/schedule-slots` | 已有 |
| 周计划项增删改 | `POST` / `PATCH` / `DELETE /api/plan-items` | 已有 |
| 生成任务 | `POST /api/week-plan/generate?date=` | 已有 |

**不新增接口、不加表、不写迁移。** 预览所需的数据（哪些已生成、哪些待生成）
后端已经在 `days[].plans[].generated` 里给全了。

---

## 四、数据流

```
日历页 ──点「周计划」──> switchTab('week')
                            │
                            ├─ body.dataset.view = 'week'   → main 放宽到 1240px
                            ├─ loadWeekPlan() ──GET /api/week-plan──> renderWeekPlan()
                            └─ URL: #/week

周计划页 ──点「月历」──> switchTab('list')
                            ├─ body.dataset.view = 'list'    → main 回到 720px
                            └─ URL: #/list
```

**状态保持**（与现状一致，切换不丢位置）：
- `_weekAnchor` —— 停在上次翻到的那一周
- `_calYear` / `_calMonth` —— 停在上次翻到的那个月

---

## 五、错误处理

| 场景 | 行为 |
|---|---|
| 周计划接口失败 | 沿用现状：`#week-content` 显示 `.error-banner` |
| 课表保存撞车（409） | 留在编辑弹窗里，错误信息可见（沿用现状，`toast` + 不关闭弹窗） |
| 生成失败 | 沿用现状：toast 提示，解除按钮禁用 |
| 预览层点取消 | 不发任何请求，直接关闭 |
| 时间轴无课 | 空态文案 + 「＋ 新增时段」，不画空坐标系 |
| 孩子端强访 `#/week` | 沿用现状：静默回退 `#/board`，不报错 |
| 时间轴刻度跨 0 点（22:00-02:00） | **本轮不支持**：结束时间必须晚于开始时间（后端 `create_slot` 已校验 `end > start`），跨天课表无法录入，不需处理 |

---

## 六、测试

### 6.1 不动的测试（红线）

| 文件 | 为什么不动 |
|---|---|
| `tests/test_week_plan_flow.py` | `#week-manage-slots` 的结构与位置不变，只换点击行为 |
| `tests/test_desktop_layout.py` | 基础 `main { max-width: 720px }` 规则保留；豁免规则写在它之后，正则仍先命中基础规则 |
| `tests/test_calendar_heatmap.py` | 视图切换器是独立控件，不碰 `.cal-mode` / `data-cal-mode` |
| `tests/test_header_ui.py` | `id="btn-week-plan"` 保留在 `index.html` |
| `tests/test_m5_week_plan.py` | `openScheduleManager` 保留（语义变为「定位到课表分区」），另加 `renderScheduleSection` |
| `tests/test_calendar_focus.py` / `test_calendar_marks.py` / `test_mobile_layout.py` / `test_density.py` | 与本次改动无交集 |

### 6.2 要改的测试

**`tests/test_slot_modal_back.py`** —— 原文断言「点『返回』退回**课表列表**（弹窗上一层）」。
课表列表不再是弹窗了，「上一层」变成页内分区。断言要改成：
次要键文案仍是「返回」（不是「取消」）、点它关闭弹窗并高亮页内分区。

**保护意图不变**：次要键 = 回到上一层，不是「撤销整件事」。

### 6.3 新增的测试

**`tests/test_week_view_switch.py`**（新增）—— 钉住：

| # | 断言 |
|---|---|
| 1 | `index.html` 有两个视图切换器，且都含 `data-view="calendar"` / `data-view="week"` |
| 2 | `id="btn-week-plan"` 仍在 `index.html`（回归保护） |
| 3 | `styles.css` 有 `body[data-view="week"] main` 的宽度豁免，且基础 `main` 的 max-width 仍在 640–820 |
| 4 | `.week-days` 在宽屏是 7 列，窄屏是 7 列 + 横向滚动 |
| 5 | 时间轴刻度范围是**动态算出来的**（断言函数存在且不写死 15:00 / 21:00） |
| 6 | 课表分区是页内 section，不在 `#modal-content` 里 |
| 7 | 生成前有预览确认层（断言预览函数存在，且「确认」前不发 POST） |

### 6.4 测试怎么跑

```bash
cd /home/xiaoduo/project/study-tracker
set -a && . ./.env >/dev/null 2>&1 && set +a
python3 -m pytest tests/ -q          # 后端 + 静态断言
npm run test:js                      # node 脚本
node --check app/static/app.js        # 语法守卫
```

---

## 七、文档同步

| 文件 | 改什么 |
|---|---|
| `docs/07-双角色改造规范.md` §1.3 | 家长端专属页表里「周计划」的入口从「日历页右上『周计划』按钮」改为「日历页『月历 \| 周计划』分段切换器（两页互切）」 |
| `docs/02-页面设计.md` §5.4 | 重写周计划页页面设计（7 列周视图 + 课表时间轴分区 + 生成预览） |
| `docs/08-重构约束与测试基线.md` §4 | 补记本轮改动的测试文件与新增测试 |
| `README.md` | 若有功能清单章节，补「周计划」入口位置 |

---

## 八、不做的事

| 不做 | 理由 |
|---|---|
| 顶部导航加第 4 个 Tab | 用户选了「日历页分段切换器」，不改双角色「3 Tab」契约 |
| 把周计划塞进 `.cal-mode` 组 | 那组语义是「月历长什么样」，周计划是一整个页面 |
| 改全局 `main` 的 max-width | `test_reading_measure_is_capped` 钉死 640–820px；且其它页面的阅读 measure 不该被牵连 |
| 课表管理单独开一个路由 `#/schedule` | 用户选了「周计划页内的独立分区」 |
| 超载 / 冲突的主动建议文案 | 本轮范围外（用户未勾选） |
| 新增后端接口 / 表 / 迁移 | 现有接口已够 |
| 前端构建工具 / npm 运行时依赖 | 项目硬约束（见 `docs/08` §1.9） |
| 外部 CDN 依赖 | 项目硬红线（`docs/08` §2.5） |

---

## 九、验收标准

| # | 场景 | 期望 |
|---|---|---|
| A1 | 1280px 视口打开日历页 | 标题行右侧有显眼的「月历 \| 周计划」分段控件，不再是灰色小字 |
| A2 | 点「周计划」 | 整页切到周计划，URL = `#/week` |
| A3 | 点周计划页顶上的「月历」 | 切回日历，URL = `#/list` |
| A4 | 1280px 视口看周计划页 | 内容列约 1240px（不再只有 688px），右侧无大片空白 |
| A5 | 周计划页主区 | 周一至周日 7 列并排，每列能放下「国际象棋 16:00-18:00」且不折成竖排 |
| A6 | 翻到上周 | 出现「回到本周」按钮，点它跳回本周 |
| A7 | 点负荷卡的「课表(分)·管理」 | 页面滚动到下方「固定课表」分区并高亮，**不开弹窗** |
| A8 | 课表分区 | 纵向时间轴 7 列；刻度按当天实际课程动态取范围；撞车的两段错开叠放且标红 |
| A9 | 点课块 | 弹窗编辑单条时段，次要键是「返回」 |
| A10 | 点「生成本周任务」 | 先出预览确认层，列出条数与分钟；取消则不写库；确认才生成 |
| A11 | 孩子端 | 日历页看不到「周计划」切换器；强访 `#/week` 静默回退 |
| A12 | 窄屏 375px | 周计划主区横向滚动，不挤成一团；课表分区同样可横向滚动 |
| A13 | 回归 | `pytest tests/ -q` 全绿；`npm run test:js` 全绿；`node --check` 通过 |
