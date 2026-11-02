# app/static/vendor

第三方前端库，本地存放以保证**离线可用**（部署环境可能完全没有外网，
或外网不通时页面不应出现功能缺失的日期选择器或图表）。

> **硬红线**：`docs/08-重构约束与测试基线.md` §2.5 与
> `tests/test_datepicker.py::test_no_cdn_dependency` 钉死「页面不得直接
> 引用外部 CDN」。这两个库都必须**本地存放**，升级时手动下载替换。

## flatpickr 4.6.13

- 官网: https://flatpickr.js.org
- 许可: MIT
- 用途: 替换原生 `<input type="date">` / `<input type="datetime-local">`。
  原生控件在 Windows 上是系统控件（灰色渐变、系统字体、图标不可定制），
  与本项目的浅色奶油糖果配色割裂。

### 文件

| 文件 | 说明 |
|---|---|
| `flatpickr.min.js` | 库本体，无外部依赖 |
| `flatpickr.min.css` | 库默认样式（会先被下面的主题覆盖） |
| `flatpickr.zh.js` | 简体中文语言包，挂载到 `window.zh` |
| `flatpickr-theme.css` | **本项目自写**的主题，覆盖库默认配色 |

`flatpickr-theme.css` 是我们自己维护的，改配色请改这个文件，
不要去改 `flatpickr.min.css` —— 那是上游文件，升级版本时会被替换。

### 升级步骤

```bash
cd app/static/vendor
curl -sLO https://unpkg.com/flatpickr@<新版本>/dist/flatpickr.min.js
curl -sLO https://unpkg.com/flatpickr@<新版本>/dist/flatpickr.min.css
curl -sLO https://unpkg.com/flatpickr@<新版本>/dist/l10n/zh.js
```

升级后**必须**跑 `pytest tests/test_datepicker.py`：那组用例锁的是接入时
踩过的坑（实例不会挂在元素上、重复扫描会多生成控件、原生 input 会变
`type=hidden`），版本升级最容易把它们重新踩一遍。

## 接入注意事项（踩过的坑）

1. **库会把原生 input 改成 `type=hidden`**，另建一个 `type=text` 的可见框。
   项目里 `collectRows()` 读的是原生框的 `.value`，必须保持 ISO 格式。
2. **库不会把实例挂在元素上**。4.6.13 源码里没有 `_fpFlatpickr` 这个字段，
   也没有 `flatpickr.getElementById()`。实例由 `app.js` 用 `_fpInstance`
   自行留存。
3. **重复扫描会生成重复控件**。库的可见框也带 `js-datepicker` 类，
   所以绑定时要按 `input.type === 'date'` 过滤，而不是只按类名。
4. **先赋值再初始化**。组件化后原生框是 hidden，直接写 `.value`
   不会同步到可见框。
5. **中文包默认周日起始**，而日历页的月历是周一起始。
   `app.js` 在 `localize()` 之前把 `zh.firstDayOfWeek` 改成 `1`。

## Apache ECharts 5.5.1

- 官网: https://echarts.apache.org
- 许可: Apache-2.0
- 用途: 家长端复盘页的图表（每日用时堆叠柱状图、学科用时占比环图、
  月内按周对比图）。2026-11-02 之前这些图是手写 SVG，4 张图里只有 2 份
  独立数据（环图 = 实际占比饼图，折线 = 柱状图数据），且手写 SVG 无法
  自动避让标签、无法缩放 31 天的月视图。
- 引入位置: `app/static/charts.js`（本项目自己的封装层，**不放业务逻辑
  在库里**，也不改上游文件）。

### 文件

| 文件 | 说明 |
|---|---|
| `echarts.min.js` | 库本体（UMD 全量构建），无外部依赖 |

- 版本: 5.5.1
- 大小: 1,030,855 bytes
- sha256: `e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf`

### 升级步骤

> ⚠️ **下载源用 unpkg，不是 jsdelivr**。2026-11-02 实测本机
> `cdn.jsdelivr.net` 连不上（HTTP 000，连接超时），`unpkg.com` 正常
> （HTTP 200）。flatpickr 那节里写的 jsdelivr 地址同样会失败，
> 一并改用 unpkg。

```bash
cd app/static/vendor
curl -sL -o echarts.min.js https://unpkg.com/echarts@<新版本>/dist/echarts.min.js
node --check echarts.min.js          # 语法守卫
sha256sum echarts.min.js             # 记进本文件
```

升级后**必须**跑：

```bash
node tests/test_chart_options.js
python3 -m pytest tests/test_chart_vendor.py -q
```

### 接入注意事项（踩过的坑）

1. **画布不认 CSS 变量**。`app.js` 的 `SUBJECT_COLORS` 存的是
   `var(--c-语)` 这种字符串，ECharts 画布拿不到 —— 必须先用
   `getComputedStyle` 把变量解成实际色值再喂给 ECharts。
   见 `charts.js` 的 `resolveCssColor()`。
2. **同一个 DOM 上二次 `init` 会警告**。复盘页切周期时
   `root.innerHTML = ''` **不会**销毁 ECharts 实例，canvas 与 resize
   监听都会泄漏。必须先 `disposeCharts()` 再清 DOM。见 `charts.js` 的
   `ChartRegistry`。
3. **没有 canvas 的环境不能建图**。jsdom（测试用）没有 `canvas` 包，
   `getContext('2d')` 返回 `null`，`echarts.init()` 直接抛
   *"Can't get context from the given item"*。`charts.js` 的
   `canRenderCharts()` 探测后**退化成 HTML 表格**，不白屏。
4. **加载顺序**：`echarts.min.js` → `charts.js` → `dual.js` → `app.js`。
   `charts.js` 要拿到 `echarts` 全局；`app.js` 末尾的 `switchTab()` 在
   加载期就会触发 `loadReview()`，可能立刻建图，所以 `charts.js` 必须
   排在 `app.js` 之前。
