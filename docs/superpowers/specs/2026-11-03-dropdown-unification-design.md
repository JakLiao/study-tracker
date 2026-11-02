# 全项目下拉统一（自绘 select 内核 + 项目/科目取消自动默认）

日期：2026-11-03
状态：已与用户确认（范围 / 视觉方向 / 窄列方案 / 默认值覆盖层 四项均已拍板），待实现
配套：`docs/02-页面设计.md`（视觉契约）、`docs/08-重构约束与测试基线.md`（红线清单）

---

## 一、背景与根因

### 1.1 需求

> 「全项目的 select 下拉选项 UI 统一美化」

### 1.2 为什么"美化原生 select"在本项目里只能落地为"换成自绘"

原生 `<select>` **展开后的候选面板由操作系统绘制**，底色、文字、圆角、高亮、滚动条
都不是我们的 —— CSS 一处也改不动。Windows Chrome 上它是一块系统面板，落在本项目的
奶油糖果配色里"像贴错了别的应用"。

这个理由项目里已经写过三次，且每次都据以改造：

| 原控件 | 已换成 | 代码依据 |
|---|---|---|
| `<datalist>` | `.combo` 自绘 | `app.js` §作业类型下拉段落 |
| 原生 `<input type="date">` | flatpickr | `app/static/vendor/flatpickr*` |
| 原生 `prompt()` / `confirm()` | `.prompt-body` / `confirm-modal` | `styles.css` §17b |
| 原生 `<select>`（项目） | `.picker` 自绘 | `app.js` §项目选择器段落 |

所以本次"统一美化"的实质是：**把剩下 9 处原生 select 全部换成自绘，并让项目里
已有的两套自绘下拉（`.combo` / `.picker`）与新组件共用一套内核与视觉词汇。**

### 1.3 现状盘点

项目里此刻有**三种**下拉形态：

| 形态 | 数量 | 用在哪 | 展开面板 |
|---|---|---|---|
| 原生 `<select>` | **9** | 科目 / 任务来源 / 星期 / 预览表科目列 | **操作系统绘制，样式不可控** |
| `.combo` | 2 | 作业类型（可自由输入） | 自绘 |
| `.picker` | 4 | 项目（可就地新建） | 自绘 |

9 处原生 select 逐条清单：

| # | id | 位置 | 选项 | 现有读取方 |
|---|---|---|---|---|
| 1 | `qa-subject` | `index.html` 快速添加面板 | 语文/数学/英语 | `app.js:2131` |
| 2 | `ed-kind` | 编辑作业弹窗 | 学科作业/培训/习惯 | `app.js:1732`(+change) `1780` |
| 3 | `ed-subject` | 编辑作业弹窗 | 语文/数学/英语 | `app.js:1783` |
| 4 | `m-kind` | 手动添加弹窗 | 学科作业/培训/习惯 | `app.js:3056`(+change) `3085` |
| 5 | `m-subject` | 手动添加弹窗 | 语文/数学/英语 | `app.js:3075` `3087` |
| 6 | `slot-weekday` | 课表时段弹窗 | 周一~周日 | `app.js:4908` |
| 7 | `pi-kind` | 周计划项弹窗 | 学科作业/培训/习惯 | `app.js:5035`(+change) `5055` |
| 8 | `pi-subject` | 周计划项弹窗 | 语文/数学/英语 | `app.js:5060` |
| 9 | `data-field="subject"` | 预览表每行的科目列 | 语文/数学/英语 | `app.js:2937` |

其中 **4 处挂了 `change` 监听**（`ed-kind` / `m-kind` / `m-subject` 经 `bindEstAutofill` /
`pi-kind`），**8 处读 `.value`**。这两个事实决定了迁移方案的形状（见 §三）。

### 1.4 两套自绘下拉已经有多接近

实测两者的样式声明（`styles.css`）：

| 属性 | `.combo-menu` / `.combo-option` | `.picker-panel` / `.picker-option` |
|---|---|---|
| 面板底色 | `var(--surface)` | `var(--surface)` ✅ 同 |
| 边框 | `var(--border)` | `var(--border)` ✅ 同 |
| 圆角 | `var(--radius-md)` | `var(--radius-md)` ✅ 同 |
| 阴影 | `var(--shadow-card)` | `var(--shadow-card)` ✅ 同 |
| 选项 padding | `space-1 space-2` | `space-1 space-2` ✅ 同 |
| 选项字号 | `fs-body-s` | `fs-body-s` ✅ 同 |
| 高亮 | hover `surface-2` / active `accent-soft` | hover `surface-2` ✅ 同 |

**差异只剩**：触发键形态（input+独立箭头按钮 vs 一整个 button）、选项元素类型
（`<li>` vs `<button>`）、面板多出的「＋新建项目」区。也就是说，"统一"不是从零设计，
而是把两套已经高度接近的实现合并到一套词汇上。

---

## 二、已确认的决策

| # | 决策 | 选择 |
|---|---|---|
| 1 | 范围 | 换掉 9 处原生 select **+** 三种下拉共用一套内核 |
| 2 | 视觉 | 沿用现有面板样式，**再打磨一轮**（勾号 / 高亮 / 行距 / 滚动条 / 贴边翻转） |
| 3 | 预览表窄列 | 改成**三个小分段按钮** |
| 4 | 自动默认值 | **科目一律不预选**；**项目在新建流程保留 `defaultFirst`**（编辑流程不预选）；任务来源、星期保留默认 |

### 2.1 决策 4 的最终口径

| 字段 | 新建流程 | 编辑已有记录 |
|---|---|---|
| **科目** | **空**，必须自己选 | 原值（没有就是空） |
| **项目** | **预选第一个**（`defaultFirst: true`） | 原值（没有就是空） |
| 任务来源 | 预选「学科作业」 | 原值 |
| 星期 | 预选「周二」 | 原值 |
| 归属日 | 预选今天 | 原值 |

**`defaultFirst` 机制保留**（上一轮 2026-11-02 引入的那套"两档策略"不动）：

- `projectPickerHtml()` 的第 4 个参数、`bindProjectPicker()` 里的 `policy`、
  HTML 上的 `data-default` 属性 —— **全部保留**；
- 新建流程（快速添加 / 手动添加 / 新增课表时段 / 新增周计划项）继续用
  `defaultFirst: true`，家长加一条培训任务时不用多点一次；
- 编辑流程（编辑作业 / 改时段 / 改计划项）继续用默认档 —— 原记录没挂项目就是空，
  **绝不替它编一个**。这正是 2026-11-02「钢琴」事故的修复，一分不能让。

`tests/test_project_picker.py` 里断言"新建流程保留默认"的两条**保持原样**，无需改写。

**「科目不自动默认」意味着原生 `<select>` 的首项自动选中行为必须被替换掉**：

| 原生 select 现状 | 新行为 |
|---|---|
| `#ed-subject` 自动选「语文」 | 空，显示「未选择科目」（原记录有值则显示原值） |
| `#m-subject` 自动选「语文」 | 空，显示「未选择科目」 |
| `#qa-subject` 自动选「语文」 | 空，显示「未选择科目」 |
| `#pi-subject` 自动选「语文」 | 空，显示「未选择科目」（原记录有值则显示原值） |
| `#ed-kind` 自动选「学科作业」 | **保留**（决策 4） |
| `#m-kind` 自动选「学科作业」 | **保留** |
| `#pi-kind` 自动选「学科作业」 | **保留** |
| `#slot-weekday` 自动选「周二」 | **保留** |

### 2.2 为什么科目与项目的取舍不同

这不是同一条规则套两个字段，而是两处取舍分别拍板的结果：

- **科目是分类断言，猜不得。** 语文 / 数学 / 英语 三选一，预选「语文」是一次
  **纯猜测** —— 家长加一条数学作业，科目静默落在语文下，看板上就归错了类，
  而界面上没有任何一处会提示。必须自己选。
- **项目是具名实体，预选只是省一次点击。** 候选是家长自己建的项目（钢琴 /
  国际象棋 / 阅读），预选第一个，家长在浮层里一眼能看出对不对；而且项目行
  旁边就挂着「＋新建项目」，说明这个字段的心智是"挑一个已有的"，不是"猜一个分类"。
  新建时省一次点击是划算的 —— 代价是**改已有记录时不能这么干**（原记录没有项目
  就得是空的），这一条已经由 `defaultFirst` 的编辑档守住。
- **任务来源与星期**同样保留默认：它们可见、且几乎不会选错 —— 任务来源默认
  「学科作业」时科目行/项目行会立刻随之显隐，家长瞄一眼表单就知道当前是什么；
  星期默认「周二」，时段列表里直接写着。

---

## 三、共享内核设计

### 3.1 统一类名词汇

三种形态共用同一套"浮层 / 选项"词汇。**只有形态特有的部分保留自己的名字。**

| 统一类 | 取代 | 用途 |
|---|---|---|
| `.dd-panel` | `.combo-menu` / `.picker-panel` | 浮层容器 |
| `.dd-list` | `.picker-list` | 选项容器（可滚动） |
| `.dd-option` | `.combo-option` / `.picker-option` | 选项行 |
| `.dd-option.is-active` | `.combo-option.is-active` | 键盘高亮项 |
| `.dd-option.is-selected` | `.picker-option.is-selected` | 当前选中项 |
| `.dd-empty` | `.combo-empty` | 无候选提示 |
| `.dd-caret` | `.picker-caret` / `.combo-toggle` 的三角字形 | 三角箭头**字形**（span） |
| `.dd-trigger` | `.picker-trigger` | 触发键（按钮形态） |
| `.dd-value` | `.picker-value` | 触发键上的当前值文本 |

形态特有、保留原名：`.combo-input`（自由输入框）、`.combo-toggle`（作业类型那枚
可点的箭头**按钮**——它的触发键是 input 自己，按钮只负责开合，语义与 `.picker` 的
整块触发键不同）、`.dd-create*`（项目新建区）、`.dd-new`（＋新建按钮）。

`.dd-caret` 统一的是**字形**：作业类型那枚按钮内部塞一个 `<span class="dd-caret">`，
项目那枚直接把 `.picker-caret` 改名。两处三角从此是同一条 CSS 规则画出来的。

**这样"统一"落在真正共用的那一层**（浮层与选项行 = 用户看到的"下拉选项 UI"），
而不是把三种不同语义硬捏成一个。

### 3.2 内核函数

```js
// 下拉内核: 三种形态(.combo / .sel / .picker)共用的开合、键盘导航、
// 高亮、选项渲染与外部点击收起。
//
// 为什么要有内核: 三处各写一份, 必然分叉 —— 面板样式、Esc 行为、方向键
// 顺序任何一处改了另外两处都要跟着改。项目里已有同款教训(styles.css §17b
// 的原生控件替换就是逐个替换, 结果每换一处都要重新对齐一次视觉)。
//
// @param root   根容器(.dd 家族)
// @param cfg    { trigger, panel, listEl, getOptions, renderOption,
//                 onPick, onOpen, onClose, emptyHtml, valueEl }
function bindDropdown(root, cfg) { ... }
```

内核职责（**三种形态完全一致**）：

1. 开合：`trigger` 点击切换；`open()` 时按需取一次选项并渲染
2. 外部点击收起：`document` 的 **capture 阶段** `pointerdown`，判断 `root.contains(e.target)`
   —— 与 `.combo` 现有实现同款（capture 是必须的：选项自身的 click 在冒泡，
   冒泡里判断 contains 会因顺序误关）
3. 键盘：`Esc` 收起、`↑`/`↓` 移动高亮、`Home`/`End` 跳首尾、`Enter` 选中高亮项、
   `Tab` 收起
4. 高亮项滚进可视区：`scrollIntoView({block:'nearest'})`，**必须做 typeof 判空** ——
   jsdom 没实现它，调用会抛 TypeError 并中断整个 keydown，键盘导航随之失灵
   （`.combo` 现有代码已踩过这个坑，注释保留）
5. 无障碍：`role="listbox"` / `role="option"` / `aria-expanded` /
   `aria-activedescendant` / `aria-selected`

### 3.3 三种形态的差异只在配置

| 形态 | 触发键 | 候选来源 | 特有能力 | 数据属性 |
|---|---|---|---|---|
| `.combo` 作业类型 | input（可自由输入） | 设置里的类型基线，按输入过滤 | 允许不在列表里的值 | `data-combo` |
| **`.sel` 定值列表（新增）** | button | 调用方给的固定列表 | —— | `data-sel` |
| `.picker` 项目 | button | `_qaProjects`（可异步刷新） | ＋就地新建 | `data-picker` |

---

## 四、`.sel` 组件契约（新增）

### 4.1 为什么必须沿用「hidden input 同 id」

`.picker` 已经在项目里验证过这条路（`app.js` §项目选择器注释原文）：

> 选中值放在一个 hidden input 里 —— id 与原来的 `<select>` 相同，于是各弹窗里
> `getElementById('xx-project').value` 的读取代码一行都不用改。

`.sel` 照抄这一条，于是 §1.3 表里的 **8 处 `.value` 读取一行都不用改**。

### 4.2 API

```js
// 定值列表下拉 (自绘, 不用原生 <select>)。
//
// 为什么不用原生: 展开后的候选面板由操作系统绘制, CSS 一处也改不动 ——
// 落在奶油糖果配色里像贴错了别的应用。同 .combo / .picker 的理由。
//
// @param id       hidden input 的 id —— 与原 <select> 同 id, 读取代码一行不改
// @param options  [[value, label], ...]
// @param value    当前值; 传 null 表示"还没选" (显示「未选择科目」)
// @param emptyText 空态文案, 默认「未选择」
function selectHtml(id, options, value, emptyText) { ... }
```

DOM 形态：

```html
<span class="dd sel" data-sel>
  <input type="hidden" id="ed-subject" value="数学">
  <button type="button" class="dd-trigger" aria-haspopup="listbox" aria-expanded="false">
    <span class="dd-value">数学</span>
    <span class="dd-caret" aria-hidden="true"></span>
  </button>
  <div class="dd-panel" hidden>
    <ul class="dd-list" role="listbox" aria-label="科目">
      <li class="dd-option" role="option" aria-selected="false" data-value="语文">语文</li>
      <li class="dd-option is-selected" role="option" aria-selected="true" data-value="数学">数学</li>
      <li class="dd-option" role="option" aria-selected="false" data-value="英语">英语</li>
    </ul>
  </div>
</span>
```

`value` 为空时，触发键上的 `.dd-value` 显示 `emptyText`（弱色 `.is-empty`），
列表里没有任何一项带 `is-selected`。

### 4.3 必须派发 `change`

原生 `<select>` 被选中时会派发 `change`。`app.js` 里有 **4 处**依赖这个事件：

| 监听点 | 用途 |
|---|---|
| `app.js:1732` `edKindSel` | 切换「科目」行 / 「项目」行的显隐 |
| `app.js:3056` `kindSel` | 同上（手动添加） |
| `app.js:2810` `bindEstAutofill` | 换科目时按科目基线重算预计用时 |
| `app.js:5035` `pi-kind` | 切换显隐（周计划项） |

所以 `.sel` 选中后必须：

```js
hidden.value = value;
hidden.dispatchEvent(new Event('change', { bubbles: true }));
```

**派发在 hidden input 上**（不是 trigger 按钮上）—— 监听者读的是
`getElementById('ed-kind').value`，事件目标必须与它一致。

### 4.4 9 处替换清单

| 原 | 换成 | 值来源 |
|---|---|---|
| `index.html` 的 `<select id="qa-subject">` | `index.html` 留空 `<div class="quick-row" id="qa-subject-row">`，由 `initQuickAdd()` 挂 `.sel`（与既有的 `qa-project-row` 同款） | 空 |
| `#ed-kind` | `selectHtml('ed-kind', KINDS, edKind)` | `item.task_kind \|\| 'subject'` |
| `#ed-subject` | `selectHtml('ed-subject', SUBJECTS, item.subject, '未选择科目')` | `item.subject` |
| `#m-kind` | `selectHtml('m-kind', KINDS, 'subject')` | 固定（保留默认） |
| `#m-subject` | `selectHtml('m-subject', SUBJECTS, null, '未选择科目')` | 空 |
| `#slot-weekday` | `selectHtml('slot-weekday', WEEKDAYS, weekday)` | 固定（保留默认） |
| `#pi-kind` | `selectHtml('pi-kind', KINDS, kind)` | `item ? item.task_kind : 'subject'` |
| `#pi-subject` | `selectHtml('pi-subject', SUBJECTS, item && item.subject, '未选择科目')` | 原值 |
| 预览表科目列 | 分段按钮（§五） | AI 解析值 |

**「项目」不在本清单里**：它本来就是 `.picker`（自绘），本次只改视觉词汇，
交互与 `defaultFirst` 策略一字不动（§2.1）。

### 4.5 连带新增的前端校验

「科目一律不预选」意味着**学科作业在新建时也是带着空科目起步**的 —— 家长必须
自己挑一个科目。后端三处模型都拒这种数据：

- `AssignmentCreate._check_kind_subject` → 「学科作业必须选择科目」
- `QuickAddRequest._check_kind_subject` → 同上
- `PlanItemCreate._check_kind_subject` → 「学科任务必须选择科目」

若不前端拦，家长会看到一句"入库失败"却不知道是哪个框的问题。四处提交入口
各加一句前置校验（与既有校验风格一致，`toast` 后 `return`，不关窗）：

| 入口 | 校验点 |
|---|---|
| 快速添加 `quickAddFromForm` | `_qaKind === 'subject'` 且科目为空 |
| 手动添加 `onOk` | `kind === 'subject'` 且科目为空 |
| 编辑作业 `onOk` | `kind === 'subject'` 且科目为空 |
| 周计划项 `onOk` | `kindVal === 'subject'` 且科目为空 |

提示文案统一用后端同一句「学科作业必须选择科目」，家长与日志看到的是同一套话。

「项目为空」**不拦**：后端允许培训/习惯任务没有项目（`eff_project is not None` 才校验
归属），家长可能确实想记一条不归属任何项目的通用任务。新建培训任务时项目会
预选第一个（§2.1），所以这条只在"家长把预选的那个取消掉"时才可能发生。

### 4.6 切换任务来源时不清空另一侧

把来源从「学科作业」切到「培训」，**科目行只是隐藏，已选的值不重置**；切回来时
原值还在。反之亦然。隐藏 ≠ 清空 —— 提交时传哪个字段由当前来源决定
（既有逻辑 `newSubject = subjectKind ? ... : null` 已经如此）。

这条对**编辑**流程尤其重要：家长可能先切到培训看看项目、再切回学科作业，
中途不该把已经选好的科目弄丢。

### 4.7 布局上的两条约束

- `.quick-row` 是 `display: flex`，`.sel` 进去后要跟 `.picker` 一样占满剩余宽度：
  `.quick-row > .sel { flex: 1 1 140px; min-width: 0; }`
- `.form-grid` 里 `.sel` 铺满所在列，与既有的 `.form-grid select` 规则等价：
  `width: 100%; min-width: 0; height: 34px;`

---

## 五、预览表科目列 → 分段按钮

### 5.1 物理约束（实测）

预览表 `table-layout: fixed; width: 100%`，容器实测 688px。各列：

| 列 | 宽 | 说明 |
|---|---|---|
| 勾 | 32px | |
| **科目** | **78px** | 单元格 padding 12px/12px → **内容区只有 54px** |
| 任务名 | auto → 156px | `min-width: 180px` 已被顶到下限 |
| 归属日 | 124px | |
| 选做 | 42px | |
| 预计(分) | 82px | |
| 置信 | 46px | |
| 原文 | 52px | |
| 操作 | 52px | |

固定列合计 508px + 任务名 180px 下限 = 688px = **容器全宽**。**全表没有余量**：
加宽科目列就会挤压任务名（"完成知能P35-36-37（连线题用直尺画线）" 这类任务名
会被截得更短，而任务名正是家长核对 AI 解析结果的依据）。

### 5.2 解法：全出血分段控件

分段控件**贴住单元格左右两侧**（该格 padding 归零），吃满整列 78px，
每段 78/3 ≈ 26px。实测：

| 项 | 实测值 |
|---|---|
| 科目列宽 | 78px（**不变**） |
| 任务名 input 宽 | 156px（**不变**） |
| 每段按钮宽 | 25.7 / 25.7 / 26.7px |
| 文字宽（11px 字号） | 22px |
| **余量** | **3.7 / 3.7 / 4.7px** |
| 表格行高 | 57px（**与现状一致**，行高由日期框决定） |

样式要点：

- 该格 `padding-left/right: 0`（与「勾」「选做」两列同样处理）
- 字号 `var(--fs-caption-sm)`（11px），`white-space: nowrap`
- 三段共享边框，中间段 `border-right-width: 0`
- 选中段用主色底 + `--accent-ink` 字色
- ≤640px 的卡片布局下该格是 `grid-template-columns: 4.5em minmax(0,1fr)`，
  控件自然铺满右列，无需额外处理

### 5.3 值怎么存

沿用 `data-field` 契约：分段按钮组里藏一个 `<input type="hidden" data-field="subject">`，
`collectRows()` 的 `get('subject').value` **一行不改**。

AI 解析出的科目必是三选一（`ParseItem.subject: Subject` 是 Literal，pydantic 校验），
所以分段控件**永远有一个选中项**，不存在"三段都没选中"的态。

---

## 六、视觉打磨（在现有样式上加一层）

| # | 打磨项 | 做法 |
|---|---|---|
| 1 | 选中项带勾号 | `.dd-option.is-selected::after` 画一个 ✓（纯 CSS 旋转的两条边框，或内联 SVG） |
| 2 | 键盘高亮更明确 | `.dd-option.is-active` 用 `--accent-soft` 底 + 左侧 3px 主色条 |
| 3 | 选项行更舒展 | padding `space-1 space-2` → `space-2 space-3`；字号维持 `fs-body-s` |
| 4 | 滚动条主题化 | `scrollbar-width: thin` + `scrollbar-color: var(--border) transparent`；WebKit 侧 `::-webkit-scrollbar` 细窄样式 |
| 5 | 浮层贴视口底部时上翻 | 展开时量 `panel.getBoundingClientRect().bottom`，超出视口则加 `.is-up` 类，改为向上弹出 |
| 6 | 面板宽度 | 浮层默认与触发键等宽（`left:0; right:0`）；`.picker` 保留既有 `min-width: 220px`（新建区要放得下输入框），`.sel` 与 `.combo` 不设 |

颜色一律走既有令牌（`--surface` / `--surface-2` / `--border` / `--accent` /
`--accent-soft` / `--text-1..3`），**不得出现写死的系统色**（项目已有断言锁这条）。

---

## 七、测试计划

### 7.1 新增 `tests/test_dropdown.py`

- 内核行为：开合、外部点击收起、Esc、↑↓/Home/End/Enter、Tab 收起、高亮滚进视口
- `.sel` 契约：hidden input 的 id 与值、选中后派发 `change`、空态文案
- 分段按钮：值落到 `data-field="subject"` 的 hidden input、选中段标记
- 样式契约：`.dd-panel` / `.dd-option` 使用主题令牌、不含写死系统色
- 默认值策略：**科目一律不预选**（新建与编辑都是）；**项目新建流程预选第一个、
  编辑流程不预选**；任务来源与星期**保留默认**

### 7.2 既有测试同步

引用旧类名的断言需机械替换（**语义断言一条不动**）：

| 文件 | 引用 | 数量 |
|---|---|---|
| `tests/test_edit_assignment.py` | `.combo-option` / `.combo` / `.combo-menu` / `.combo-input` / `.combo-toggle` | 12 |
| `tests/test_project_picker.py` | `.picker-value` → `.dd-value` | 1 |

`tests/test_project_picker.py` 里断言"新建流程保留默认"的两条**保持原样** ——
`defaultFirst` 机制不变（§2.1）。

### 7.3 回归基线

`python3 -m pytest tests/ -q` 与 `npm run test:js` 全绿；本轮基线为
**pytest 1083 passed / node 全绿**。

---

## 八、不做什么（YAGNI）

| 不做 | 原因 |
|---|---|
| 不新建 `dropdown.js` | 现有 jsdom harness 全部 `w.eval(app.js)`，新建文件要改十几个 harness，风险大于收益 |
| 不把 `.combo` 的"自由输入"与 `.picker` 的"可新建"合并成一种能力 | 语义确实不同，硬合并会让 `.combo` 变复杂、`.picker` 变别扭 |
| 不给原生 `<select>` 做渐进增强降级 | 项目已确立"OS 绘制的面板一律换掉"的规矩，没有降级先例 |
| 不改预览表其余列宽 | 全表已无余量，动任何一列都会牺牲任务名可读性 |
| 不动后端 | 后端契约（科目必填/禁挂）本就正确，前端只是没按它来 |

---

## 九、验收标准

1. 项目里**不再有原生 `<select>` 元素**（`app/static/index.html` 与 `app/static/app.js`
   的模板字符串里都为 0；注释里提到 `<select>` 的说明文字不算）
2. 三种下拉的浮层与选项行**共用同一套 CSS 类**（`.dd-panel` / `.dd-list` / `.dd-option`）
3. 8 处 `.value` 读取与 4 处 `change` 监听的调用代码**未被改动**
4. 打开编辑弹窗，**没有项目**的作业：项目行显示「未选择项目」且不自动选中
5. 打开编辑弹窗，**有项目**的作业：项目行显示原项目名
6. 手动添加作业：科目**不预选**，未选科目时提交被拦下并提示「学科作业必须选择科目」
7. 新建培训任务：项目**仍预选第一个**（`defaultFirst` 机制未动）
8. 预览表科目列为三段按钮，行高与任务名列宽**与改动前一致**
9. 键盘可达：Tab 聚焦 → ↓ 移动 → Enter 选中 → Esc 收起，全程可见焦点环
10. `pytest tests/` 与 `npm run test:js` 全绿
