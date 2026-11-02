# 复盘页「上月」周期 + 历史月份回看 · 设计

日期：2026-11-03
状态：已与用户确认，待实现

## 一、背景与目标

复盘页现在只有 `本周 / 上周 / 本月` 三个周期。

`本月` 是 month-to-date（本月 1 日 ~ 今日）。2026-11-03 时它只有 3 天数据 ——
柱状图上三根柱子、月报写着"本月至今"。家长真正会问的那句「上个月过得怎么样」
在界面上没有入口：复盘页没有上月的数据，日历页只能看"哪天做了多少"，
翻月翻到 10 月也只是格子换了一遍，没有分析。

本期做两件事：

1. 复盘页加 `上月` 周期，内容与 `本月` **同构**（用时统计 + 每日用时柱状图 +
   学科占比环 + 偏差表 + 月报卡 + 月内按周对比 + 薄弱项）。
2. `上月` 里可**往前翻历史月份**，回到任何一个过去月份。

## 二、已确认的产品决策

| # | 决策 | 说明 |
|---|---|---|
| 1 | **上月 = 与「本月」同构的完整复盘** | 不是只加一张月报卡。家长在「上月」看到的块与「本月」一一对应，切过去不用重新学。 |
| 2 | **可往前翻历史月份** | 上月周期内加月份步进器，能回到任何一个过去月份。 |
| 3 | **允许翻到空月份** | 不新增"有哪些月份"接口。翻到没有作业记录的月份显示空态提示，而不是禁用按钮。 |
| 4 | **步进器不覆盖本月** | 本月有独立 tab 且是 month-to-date 语义，步进器最远只能到"上个月"，避免同一个月份有两种口径。 |
| 5 | **上月是完整月，环比基准是上上月整月** | 不能照抄「本月」的"与上月同期比"。见 §三。 |

不采纳：把「本月」也改成整月（会毁掉 month-to-date 的日均口径）；做日粒度
周期；把周报扩展到历史周；改孩子端（孩子端只有本周，见 `app.js` 的角色分支）。

## 三、口径：两种"月"的环比基准不一样

这是整个改动里最容易写错的一处。

| 周期 | 统计区间 | 环比基准 | 对比文案 |
|---|---|---|---|
| 本月 | 本月 1 日 ~ 今日 | 上月**同期**（同样天数） | 与上月同期相比多用了 X 分钟 (+Y%) |
| 上月 / 历史月 | 该月 1 日 ~ 该月最后一天（整月） | 上上月**整月** | 与上上月相比多用了 X 分钟 (+Y%) |
| 2026-08 | 2026-08-01 ~ 2026-08-31 | 2026-07 整月 | 与7月相比多用了 X 分钟 (+Y%) |

为什么不能一律用"同期"：

- 本月才过了 3 天，拿上月整月（30 天）比会得出"少用了 95%"这种把
  "月还没过完"当成"孩子变懒了"的假结论 → 必须同期对齐（现状行为，不改）。
- 上月是**完整**的 31 天，拿它跟"上上月的前 31 天"比没有意义 —— 上上月
  只有 30 天，"同期"会被截断成 30 天，那其实是整月比，标签却写着"同期"，
  家长按字面理解会对不上账 → 必须标"整月"。

**结论：后端在月报 payload 里直接给出文案依据，前端不猜、不写死"本月"。**

月报 payload 新增三个字段：

| 字段 | 取值 | 用途 |
|---|---|---|
| `month_label` | `本月` / `上月` / `8月` | 所看月份的名字（相对今天） |
| `prev_month_label` | `上月` / `上上月` / `7月` | 环比基准月份的名字 |
| `prev_month_basis` | `same_period` / `full_month` | 基准是"同期"还是"整月" |

前端据此拼句子：`与${prev_month_label}${basis === 'same_period' ? '同期' : ''}相比`。

## 四、后端改动

### 4.1 新的 period 取值 `last_month`

`app/services/analytics_service.py`

- `PERIOD_LABELS` 增加 `last_month: 上月`。
- `_period_range(period, today, month=None)` 增加 `last_month` 分支：
  区间 = 指定月的 1 日 ~ 该月最后一天（**整月，不是至今**）。
  `month` 缺省时 = 上个月。
  - 过去的整月没有"未来"的问题，所以不套 month-to-date 那条规则。
  - `_last_day_of_month()` 是本模块已有的函数，直接复用。
- `build_period_analytics()` 与 `build_daily_time_stats()` 多一个 `month=None`
  参数，透传给 `_period_range()`。

`app/routers/analytics.py`

- `PERIOD_PATTERN` 扩成 `^(current|last|month|last_month)$`。
- 两条接口都加可选查询参数 `month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")`。
  - 不传 = 上月（对 `last_month` 而言）
  - 传 `2026-10` = 那个月
- 非法 `month` 由 FastAPI 的 pattern 直接拒（422）。

`/api/monthly-report?month=YYYY-MM` 已经支持任意月份（`app/routers/report.py`
的 `_parse_month`），**不用改**。

### 4.2 月报的口径字段

`app/services/analytics_service.py` 的 `build_month_analytics()` 增加三个字段
（见 §三的表）。判定：

- `month_label`：该月 == 本月 → `本月`；== 上月 → `上月`；否则 `"{月}月"`。
- `prev_month_label`：基准月 == 上月 → `上月`；== 上上月 → `上上月`；
  否则 `"{月}月"`。
- `prev_month_basis`：该月是当前月（`is_partial`）→ `same_period`；
  否则 `full_month`。

`app/services/report_service.py`

- `_month_highlights()` / `_month_suggestions()` 里写死的"本月"改成
  `analytics["month_label"]`。
- 环比句按 §三 的模板拼，不再写死"上月同期"。
- `build_monthly_report()` 把三个新字段透传到 payload。

## 五、前端改动

### 5.1 界面

`app/static/index.html`

- 周期按钮行加第 4 个按钮：
  `<button data-period="last_month" class="ghost period-btn">上月</button>`
- 按钮行下方加月份步进器（仅"上月"周期显示）：

```
┌────────────────────────────────────────┐
│  ‹        2026年10月        ›   回到上月 │
└────────────────────────────────────────┘
```

- 样式沿用日历页的箭头风格（`← 上月` / `下月 →` 那套 ghost 按钮）。

### 5.2 状态与逻辑

`app/static/app.js`

- `PERIOD_LABEL` 增加 `last_month: '上月'`。
- 新增 `isMonthPeriod(p)` = `p === 'month' || p === 'last_month'`，
  **所有**"是不是月视图"的判断都走它。
  现状散落着 `r.period === 'month'`（`renderReviewTimeBlock` /
  `renderBuckets` / `renderSubjectShare` 三处），逐个改成 `isMonthPeriod()`。
- 新增 `_reviewMonth`（`'YYYY-MM'`）：当前正在看的月份。
  - 切走再切回复盘页时保留（与日历页保留 `_calYear/_calMonth` 同思路）。
  - 首次进入"上月"周期时 = 上个月。
- 步进器：`‹` 往前一个月，`›` 往后一个月（最远到上个月），`回到上月` 跳回上个月。
  - `›` 在"上个月"处 disabled（不覆盖本月，见决策 4）。
  - `‹` 永远可用（决策 3：允许翻到空月份）。
- `loadReview(period)`：
  - `last_month` 周期时，`/analytics/review` 与 `/analytics/review-time`
    带上 `&month=YYYY-MM`；`/monthly-report` 带 `?month=YYYY-MM`。
  - `month` 周期维持现状（不带参数）。
- `renderMonthReport(month)`：
  - 标题 `${month_label}课后月报`（本月 → 本月课后月报；上月 → 上月课后月报）。
  - 区间文案：partial → `${label}至今 N 天`；整月 → `整月 MM-DD ~ MM-DD`。
  - 统计项 `${label}周数`。
  - 口径说明的环比基准按 §三 拼。
  - 建议标题：本月 → `下个月可以这样`；其他月份 → `接下来可以这样`
    （历史月份的"下个月"是有歧义的）。
- `renderMonthWeeksChart(month)`：卡片标题 `${label}按周对比`。

`app/static/dual.js`

- `reflectionTagDates(period, month)`：`last_month` 周期要读**那个月**的
  晚间小结日期，不是"最近 31 天"。
  现状是按 period 推日期（current=近 7 天 / last=7~1 天前 / month=近 31 天），
  上月周期必须换成所选月份的实际日期。
- `renderParentReviewExtras(period, ctx)`：从 `ctx.month` 拿月份。

### 5.3 空月份

翻到没有作业记录的月份时：

- 月报卡显示"该月还没有作业记录"（后端 `completion.total == 0` 的既有分支，
  文案改成 `${label}还没有作业记录`）。
- **不发**晚间小结的标签请求。`collectReflectionTags()` 是"一天一个请求"，
  一个空月份就是 31 个必然 404 的请求。判据：该月既没有已完成的作业
  （用时统计里没有一分钟）也没有偏差样本 → 不画薄弱项卡。

这条优化只对 `last_month` 周期生效，不动 `本月` 的既有行为。

## 六、数据流

```
用户点「上月」
   └─ loadReview('last_month')
        ├─ GET /api/analytics/review-time?period=last_month&month=2026-10
        ├─ GET /api/analytics/review?period=last_month&month=2026-10
        ├─ GET /api/monthly-report?month=2026-10
        └─ (有数据时) GET /api/daily-wrap?date=... × 该月天数
   └─ 渲染：每日用时柱状图 + 学科占比环 + 偏差表 + 月报卡 + 按周对比 + 薄弱项
```

三个接口的统计区间必须一致（`2026-10-01 ~ 2026-10-31`），否则三处数字对不上账。
三处都走同一个 `_period_range`，所以天然一致 —— 这是把 `last_month` 做成
**period 取值**而不是前端另写一套区间的原因。

## 七、测试

`tests/test_last_month_review.py`（新增）

后端（pytest）：

1. `_period_range('last_month')` = 上个月 1 日 ~ 上个月最后一天（整月，含月末日）。
2. 传 `month=2026-10` 时区间 = 2026-10-01 ~ 2026-10-31。
3. `month=2026-02` 时区间 = 2026-02-01 ~ 2026-02-28（闰年/平年都正确）。
4. `month=2026-12`（未来的月）取整月 —— 但前端不会发这种值，后端按整月处理即可。
5. `period=banana` 仍被拒（回归）。
6. 月报 payload 的 `month_label` / `prev_month_label` / `prev_month_basis`
   在三种情形下取值正确。
7. 上月周期下，月报、区间、每日用时三处 `period_start/period_end` 一致。

前端（jsdom，仿 `tests/test_review_period_report.py` 的 harness）：

8. 点「上月」→ 出现「上月课后月报」卡，且**不出现**「本周课后周报」。
9. 点「上月」→ `/monthly-report?month=<上个月>` 被请求，且带的是上个月。
10. 步进器 `‹` 点一次 → 月份标签退一个月，且 `/monthly-report?month=` 跟着变。
11. `›` 在上个月处 disabled。
12. 切到别的 tab 再切回「上月」→ 月份不变（状态保留）。
13. 回归：本周/上周/本月三个 tab 行为不变（周报只在本周出现）。

## 八、不做

- 不做日粒度周期（"昨天""前天"）。
- 不把周报（`/weekly-report`）扩展到历史周 —— 它永远算本周。
- 不改孩子端：孩子端只有本周成绩单，`.period-btn` 的角色过滤已经藏掉非本周按钮。
- 不做月份的日历选择器（下拉选月份）—— 步进器够用，少一个控件少一套键盘可达性问题。
