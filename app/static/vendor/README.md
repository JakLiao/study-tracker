# app/static/vendor

第三方前端库，本地存放以保证**离线可用**（部署环境可能完全没有外网，
或外网不通时页面不应出现功能缺失的日期选择器）。

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
curl -sLO https://cdn.jsdelivr.net/npm/flatpickr@<新版本>/dist/flatpickr.min.js
curl -sLO https://cdn.jsdelivr.net/npm/flatpickr@<新版本>/dist/flatpickr.min.css
curl -sLO https://cdn.jsdelivr.net/npm/flatpickr@<新版本>/dist/l10n/zh.js
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
5. **中文包默认周日起始**，而列表页的月历是周一起始。
   `app.js` 在 `localize()` 之前把 `zh.firstDayOfWeek` 改成 `1`。
