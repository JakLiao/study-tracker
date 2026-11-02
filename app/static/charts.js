/* charts.js —— 家长端复盘页的图表层 (ECharts 封装)
 *
 * 为什么单独一个文件:
 *   复盘页的图以前是手写 SVG 字符串, 散落在 app.js (柱状图 + 两张饼图)
 *   与 dual.js (环形图 + 折线图) 里。2026-11-02 实测发现: 页面上有 4 张图,
 *   但只有 2 份独立数据 —— 环形图 = "实际时间占比" 饼图, 折线图 = 柱状图的
 *   同一批 days 数据。根因是 10-29 加的 app.js 图表与 11-01 双角色改造加的
 *   dual.js 图表叠在了一起, 当时只改了标题避重, 没做去重。
 *
 *   本文件把这块收成两层:
 *     纯函数层  buildDailyTimeOption / buildSubjectShareOption /
 *               buildMonthWeeksOption —— 只吐 ECharts option, 不碰 DOM,
 *               可以直接丢进 node 里测 (不需要 canvas)。
 *     渲染层    renderChart / disposeCharts —— 管 ECharts 实例的生命周期。
 *
 * 加载顺序 (index.html):
 *   vendor/echarts.min.js → charts.js → dual.js → app.js
 *   charts.js 排在 app.js 之前是必须的: app.js 末尾的 switchTab() 在加载期
 *   就会触发 loadReview(), 可能立刻建图。charts.js 里对 app.js 函数
 *   (subjectColor / orderedSubjects / sharePercents) 的引用**只在函数体内**,
 *   调用期才解析 —— 顶层不做任何跨文件调用。
 *
 * 注意: 这个文件**不能**加 'use strict'。它与 app.js / dual.js 一样是
 * 经典脚本, 但 tests/ 里的 jsdom harness 是用 window.eval() 加载它的 ——
 * 严格模式下 eval 里的函数声明**不会**泄漏到全局作用域, 于是 app.js
 * 调用 buildDailyTimeNote() 时会 ReferenceError (实测踩过)。
 */

// ====== 颜色令牌 ======
//
// ECharts 画在 canvas 上, **不认 CSS 变量**。app.js 的 SUBJECT_COLORS 存的是
// 'var(--c-语)' 这种字符串, 直接喂给 ECharts 会解析失败。所以每次建图都要把
// 变量解成实际色值再传进去。
//
// 注意: 角色切换 (parent / kid) 会换掉令牌值, 所以色值**不能缓存** ——
// 每次重画都重新 resolve 一次。

function readToken(name) {
  try {
    var cs = typeof getComputedStyle === 'function'
      ? getComputedStyle(document.documentElement)
      : null;
    if (!cs || typeof cs.getPropertyValue !== 'function') return '';
    return (cs.getPropertyValue(name) || '').trim();
  } catch (e) {
    return '';
  }
}

// 把 'var(--c-语)' 解析成 '#BF3B2E'。
//
// 两条路径:
//   1. 现代浏览器的 getComputedStyle 已经把 var() 替换掉了 —— 直接返回;
//   2. 不替换的环境 (部分 jsdom) 返回的还是 'var(--s-yu-c)' —— 递归解一层。
//
// 解不出来时**原样返回**: 让 ECharts 用它自己的默认色, 好过给一个错的
// 学科色 (颜色对不上科目比颜色缺失更糟)。这里刻意不硬编码一份色值表 ——
// 那是 app.js:3771 SUBJECT_COLORS 的职责, 两处各一份迟早会漂。
function resolveCssColor(v) {
  var s = String(v == null ? '' : v).trim();
  if (!s) return '';
  // 变量名里可以有中文! 本项目的科目色令牌是 --c-语 / --c-数 / --c-英,
  // 所以这里**不能**用 \w 匹配名字 (它只认 [A-Za-z0-9_]) —— 早先写成
  // /--[\w-]+/ 时, 所有科目色都解不出来, ECharts 拿到的还是 var(--c-语),
  // 整张图会落回它自己的默认调色板, 颜色全对不上科目。
  var m = /^var\(\s*(--[^\s,)]+)\s*\)$/.exec(s);
  if (!m) return s;
  var got = readToken(m[1]);
  if (!got) return s;
  if (/^var\(/.test(got)) return resolveCssColor(got);
  return got;
}

// 学科色的唯一来源是 app.js 的 subjectColor()。这里只负责把它的返回值解析成
// 实际色值 —— 不另建一份「科目→色」表 (app.js 的注释写明了为什么只能有一处)。
function subjectColorOf(name) {
  if (typeof globalThis.subjectColor === 'function') {
    return resolveCssColor(globalThis.subjectColor(name));
  }
  return '';
}

// 科目的固定顺序 (语文→数学→英语)。同样是 app.js 的 orderedSubjects(),
// 不能在 charts.js 里重写一份 —— 见 app.js:187 的注释。
function subjectsInOrder(bySubject) {
  if (typeof globalThis.orderedSubjects === 'function') {
    return globalThis.orderedSubjects(bySubject);
  }
  return Object.keys(bySubject || {});
}

// 百分比用最大余数法取整, 保证几个数加起来正好 100%。
// 函数在 app.js (sharePercents), charts.js 只调用不复制 —— 这样
// tests/test_subject_share_pies.js 里那组断言仍然守着同一份实现。
function percentsOf(mins) {
  if (typeof globalThis.sharePercents === 'function') {
    return globalThis.sharePercents(mins);
  }
  var total = mins.reduce(function (s, m) { return s + m; }, 0);
  if (!total) return mins.map(function () { return 0; });
  var out = mins.map(function (m) { return Math.floor(m / total * 100); });
  var rest = 100 - out.reduce(function (s, v) { return s + v; }, 0);
  for (var i = 0; rest > 0; i++, rest--) out[i % out.length] += 1;
  return out;
}

// 按"当前看得见的那几天"重新汇总各科用时 (预估 / 实际分开)。
//
// 为什么需要这一份: 后端 subject_share 是**整段**的合计, 而逐日柱状图
// 会被"显示周末"开关过滤掉周末那两天。周末没有上学, 但孩子可能写了
// 作业 —— 那 10 分钟是真的花掉了 (2026-11-02 实测: 10/31 与 11/01 各
// 10 分钟语文)。两张图摆在同一个视口里, 一个合计 685 一个 705,
// 家长只会以为图画错了。所以图上用的占比必须由**可见的那几天**重算。
//
// 实际 = 逐日 by_subject (即 COALESCE(actual_minutes, est_minutes));
// 预估 = 逐日 est_by_subject (2026-11-02 给后端补的字段)。
//
// 返回 null 表示这份数据里没有逐日预估 (老 payload / 别的接口),
// 调用方这时该保留原来的整段合计, 而不是悄悄显示一个空环。
function shareOfDays(days) {
  var list = days || [];
  if (!list.length) return null;
  var est = {};
  var act = {};
  for (var i = 0; i < list.length; i++) {
    var d = list[i] || {};
    var e = d.est_by_subject;
    if (!e || typeof e !== 'object') return null;
    Object.keys(d.by_subject || {}).forEach(function (s) {
      act[s] = (act[s] || 0) + (d.by_subject[s] || 0);
    });
    Object.keys(e).forEach(function (s) {
      est[s] = (est[s] || 0) + (e[s] || 0);
    });
  }
  function total(o) {
    return Object.keys(o).reduce(function (s, k) { return s + o[k]; }, 0);
  }
  return {
    est: { by_subject: est, total_minutes: total(est) },
    actual: { by_subject: act, total_minutes: total(act) },
  };
}

// ====== 能力探测 ======
// 没有 canvas 就建不了图。
//
// 这条不是防御性编程, 是有实际触发场景的: tests/ 里的 jsdom harness 会加载
// app.js, 而 jsdom 没有装 canvas 包, getContext('2d') 返回 null,
// echarts.init() 会直接抛 "Can't get context from the given item"。
// 所以调用方必须先问一句, 拿不到图就退化成 HTML 表格 (不白屏)。
function canRenderCharts() {
  if (typeof globalThis.echarts === 'undefined') return false;
  try {
    return !!document.createElement('canvas').getContext('2d');
  } catch (e) {
    return false;
  }
}

// ====== 纯 option 构造函数 ======

// 段内数字标签的显示门槛: 该段占当天总量的比例低于这个值就不写字。
//
// 原来手写 SVG 时用的是像素高度 'h >= 13' —— 像素高度随画布尺寸变, 换个
// 屏幕宽度结论就变了。比例不随尺寸变, 稳。
var SEG_LABEL_MIN_RATIO = 0.06;

// 每天一根柱子, 柱内按科目堆叠。区间长度不写死: 周视图 7 天, 月视图 31 天。
//
// opts = { withWeekend: bool }
function buildDailyTimeOption(r, opts) {
  opts = opts || {};
  var days = (r && r.days) || [];
  // 柱子数必须按实际天数算, 不能写死 7 —— 月视图有三十来天。
  const n = days.length;
  // 视图密度决定能画多少东西。一周 7 天每格近 94px, 什么都能写;
  // 本月三十来天每格只有 21px —— 星期名、柱内数字、柱顶总数都塞不下,
  // 硬画出来是互相压字, 比不画更难读。所以密集时只留"哪几天高"这个信息。
  const dense = n > 10;

  var dayTotals = days.map(function (d) { return d.total_minutes || 0; });
  var maxTotal = dayTotals.reduce(function (m, v) { return v > m ? v : m; }, 0);

  // 纵轴上限取"好看的整数", 再由它均分 2 段 —— 直接对 max 取整到 10
  // 会得到 190 这种刻度, 均分后是 190/95/0, 中间那档不是整数, 家长读图
  // 时得自己在脑子里换算。按 1/2/5 阶梯向上取, 两档都落在整数上。
  var step = maxTotal <= 50 ? 50 : maxTotal <= 100 ? 50 : maxTotal <= 200 ? 100
            : maxTotal <= 400 ? 200 : Math.ceil(maxTotal / 200) * 200;
  var niceMax = Math.max(step, Math.ceil(maxTotal / step) * step);

  // 科目顺序固定 语->数->英 (与后端一致), 而不是按当天的出现顺序 ——
  // 否则周一黄在底、周二蓝在底, 家长没法把颜色稳定地对应到科目。
  var allBySubject = {};
  days.forEach(function (d) {
    Object.keys(d.by_subject || {}).forEach(function (k) {
      allBySubject[k] = (allBySubject[k] || 0) + (d.by_subject[k] || 0);
    });
  });
  var subs = subjectsInOrder(allBySubject);

  var text3 = resolveCssColor(readToken('--text-3') || '#8B8377');
  var text2 = resolveCssColor(readToken('--text-2') || '#575046');
  var border = resolveCssColor(readToken('--border') || '#E5E0D6');
  var surface2 = resolveCssColor(readToken('--surface-2') || '#FAF9F6');
  var accent = resolveCssColor(readToken('--accent') || '#1D4E7C');
  var fontNum = readToken('--font-num') || 'sans-serif';

  var series = [];
  subs.forEach(function (name) {
    series.push({
      name: name,
      type: 'bar',
      stack: 'total',
      emphasis: { focus: 'series' },
      itemStyle: { color: subjectColorOf(name) },
      label: {
        show: true,
        position: 'inside',
        color: '#fff',
        fontSize: 10,
        fontWeight: 600,
        fontFamily: fontNum,
        // 段内数字白字 + 描边, 与原 .wt-seg 同一套做法。
        textBorderColor: 'rgba(0,0,0,0.22)',
        textBorderWidth: 2,
        formatter: function (p) {
          var total = dayTotals[p.dataIndex] || 0;
          if (!total) return '';
          var v = Number(p.value) || 0;
          // 太窄的段塞数字会压到相邻段上, 三个数字叠在一起比不画更难读。
          if (v / total < SEG_LABEL_MIN_RATIO) return '';
          return String(v);
        },
      },
      // 标签互相压住时 ECharts 自己藏掉一个 —— 原手写 SVG 里的
      // pieLabelTier / h>=13 两套手工判断都由它接手。
      labelLayout: { hideOverlap: true },
      data: days.map(function (d) { return (d.by_subject || {})[name] || 0; }),
    });
  });

  // 不上学的日子: 虚线轮廓的小方块, 不给任何"用时"暗示。
  // 必须与"用时 0"在视觉上分开 —— 前者是"没统计", 后者是"统计了, 确实没花时间"。
  var offIdx = [];
  days.forEach(function (d, i) { if (!d.is_school_day) offIdx.push(i); });
  if (offIdx.length) {
    series.push({
      name: '不上学',
      type: 'bar',
      // 单独一个 stack: 它不参与科目的堆叠高度, 不能把总数撑大。
      stack: 'off',
      barGap: '-100%',
      itemStyle: {
        color: surface2,
        borderColor: text3,
        borderWidth: 1,
        borderType: 'dashed',
      },
      label: { show: false },
      data: days.map(function (d, i) {
        return offIdx.indexOf(i) >= 0 ? Math.max(1, niceMax * 0.03) : null;
      }),
    });
  }

  // 最高的一天 / 异常高点标出。沿用原折线图的判定: 超过中位数 1.5 倍算异常。
  var note = buildDailyTimeNote(r);
  var hot = hotDayIndexes(days);
  var late = resolveCssColor(readToken('--st-late-c') || '#C2321F');
  var surface = resolveCssColor(readToken('--surface') || '#FFFFFF');
  var markPointData = [];
  if (hot.length) {
    hot.forEach(function (i) {
      markPointData.push({
        coord: [i, dayTotals[i]],
        value: dayTotals[i],
        itemStyle: { color: late },
        label: {
          formatter: String(dayTotals[i]),
          // 标签放在柱子**顶上方的空白里**, 不压在柱身上:
          // 压在柱身上时会盖住最上面那一段的段内数字 (2026-11-02 实测:
          // 周五那根柱顶的 "190" 正压在绿色段的 "40" 上, 两个数叠在一起)。
          position: 'top',
          distance: 5,
          color: late,
          backgroundColor: surface,
          borderColor: late,
          borderWidth: 1,
          borderRadius: 3,
          padding: [1, 4],
          fontSize: 10,
          fontWeight: 700,
          fontFamily: fontNum,
        },
      });
    });
  }

  var pa = (r && r.plain_avg) || {};
  var plainAvg = pa.total_minutes || 0;

  var option = {
    aria: { enabled: true },
    animationDuration: 260,
    // 顶上留一条空白给"异常高点"的角标 —— 它标在柱顶上方, 没地方放
    // 就会被网格切掉 (或压到坐标轴名字上)。
    grid: { left: 44, right: 18, top: markPointData.length ? 34 : 26, bottom: dense ? 56 : 46, containLabel: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      confine: true,
    },
    xAxis: {
      type: 'category',
      data: days.map(function (d) { return String(d.date || '').slice(5); }),
      axisLabel: {
        color: text2,
        fontSize: 10,
        fontFamily: fontNum,
        // 密集时让 ECharts 自己挑间隔, 否则三十来个日期会连成一片。
        interval: dense ? 'auto' : 0,
        hideOverlap: true,
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: border } },
    },
    yAxis: {
      type: 'value',
      name: '分钟',
      nameTextStyle: { color: text3, fontSize: 10, align: 'right' },
      max: niceMax,
      splitNumber: 2,
      axisLabel: { color: text3, fontSize: 10, fontFamily: fontNum },
      splitLine: { lineStyle: { color: border, type: 'dashed' } },
    },
    series: series,
  };

  // 参照线与最高点标在**第一个** series 上 —— ECharts 的 markLine /
  // markPoint 是 series 级配置, 挂在任何一个 series 上都画在同一坐标系里。
  if (series.length) {
    if (plainAvg > 0) {
      series[0].markLine = {
        silent: true,
        symbol: 'none',
        lineStyle: { type: 'dashed', color: text3 },
        label: {
          formatter: '平日日均 ' + plainAvg + ' 分',
          position: 'insideEndTop',
          color: text3,
          fontSize: 10,
          fontFamily: fontNum,
        },
        data: [{ yAxis: plainAvg }],
      };
    }
    if (markPointData.length) {
      // symbolSize 给 1: 只留标签, 不要那个红点 —— 红点正好压在最上面
      // 那一段的段内数字上 (label 已经带了红字 + 白底, 够显眼了)。
      series[0].markPoint = { symbol: 'circle', symbolSize: 1, data: markPointData };
    }
  }
  // 密集时给一个缩放把手: 月视图三十一天挤在一屏里, 家长想看清某一周时
  // 可以拖。稀疏时 (一周 7 天) 不加这个控件, 免得页面多一块无用零件。
  if (dense) {
    option.dataZoom = [
      { type: 'inside', start: 0, end: 100 },
      {
        type: 'slider', height: 16, bottom: 8,
        start: 0, end: 100,
        borderColor: border, fillerColor: 'rgba(0,0,0,0.04)',
        handleStyle: { color: accent },
        textStyle: { color: text3, fontSize: 10 },
      },
    ];
  }
  return option;
}

// 异常高点的下标: 超过中位数 1.5 倍的日子。
// 与原折线图 (dual.js 的 renderPLineChart) 同一套判定。
function hotDayIndexes(days) {
  var vals = days.map(function (d) { return d.total_minutes || 0; });
  if (!vals.length) return [];
  var sorted = vals.slice().sort(function (a, b) { return a - b; });
  var median = sorted.length % 2
    ? sorted[(sorted.length - 1) / 2]
    : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
  if (!(median > 0)) return [];
  var out = [];
  vals.forEach(function (v, i) { if (v > median * 1.5) out.push(i); });
  return out;
}

// 图表下方的说明文字。跟着数据走: 有异常点就点名, 没有就换一句最高的那天,
// 不能写死"标红的点明显高于平时"却一个红点都没有 (家长会以为页面没加载全)。
function buildDailyTimeNote(r) {
  var days = (r && r.days) || [];
  if (!days.length) return '';
  var vals = days.map(function (d) { return d.total_minutes || 0; });
  var sorted = vals.slice().sort(function (a, b) { return a - b; });
  var median = sorted.length % 2
    ? sorted[(sorted.length - 1) / 2]
    : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
  if (!(median > 0)) return '';
  var maxV = vals.reduce(function (m, v) { return v > m ? v : m; }, 0);
  var hot = hotDayIndexes(days);
  if (hot.length) {
    var who = hot.map(function (i) {
      return (days[i].weekday_label || '') + ' ' + vals[i] + ' 分钟';
    }).join('、');
    return '中位数 ' + Math.round(median) + ' 分钟, ' + who + '明显高于平时';
  }
  var top = vals.indexOf(maxV);
  return '中位数 ' + Math.round(median) + ' 分钟, 最高的一天是 '
    + (days[top].weekday_label || '') + ' ' + maxV + ' 分钟, 这段时间比较平稳';
}

// 各科时间占比环图。mode = 'est' (预估) | 'actual' (实际)。
//
// 预估与实际**合成一张环 + 一个切换**, 不再是两张并排的饼 ——
// 合成一个环的话每个科目占两段, 一眼看过去全是"语文语文语文", 想比较
// "当初估的分布"与"实际发生的分布"完全无从下手; 但并排两张环和"实际占比"
// 那一列又长得一模一样, 家长以为是同一张图出了 bug。所以给一个环 + 切换:
// 想比的时候切一下, 不用的时候只占一张图的位置。
//
// 总量为 0 时返回 null —— 调用方据此显示空态, 不画空饼 (一个只有灰色底的
// 圆会被误读成"占比为 0")。
function buildSubjectShareOption(bySubject, total, mode) {
  var subs = subjectsInOrder(bySubject).filter(function (s) { return (bySubject[s] || 0) > 0; });
  if (!subs.length || !total) return null;
  var mins = subs.map(function (s) { return bySubject[s] || 0; });
  var pcts = percentsOf(mins);

  var text3 = resolveCssColor(readToken('--text-3') || '#8B8377');
  var fontNum = readToken('--font-num') || 'sans-serif';

  return {
    aria: { enabled: true },
    animationDuration: 260,
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: function (p) {
        var pct = (p.data && p.data.pct != null) ? p.data.pct : p.percent;
        return p.name + '<br/>' + p.value + ' 分钟 (' + pct + '%)';
      },
    },
    series: [{
      name: mode === 'est' ? '预计时间占比' : '实际时间占比',
      type: 'pie',
      radius: ['58%', '82%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: true,
      // 扇形里直接写科目名 + 百分比, 不用在图例和色块之间来回对照。
      // 放不下由 labelLayout.hideOverlap 自动藏掉 —— 原手写 SVG 里的
      // pieLabelTier 三档手工判断由它接手。
      label: {
        show: true,
        position: 'inside',
        color: '#fff',
        fontSize: 12,
        fontWeight: 600,
        fontFamily: fontNum,
        textBorderColor: 'rgba(0,0,0,0.22)',
        textBorderWidth: 2,
        formatter: function (p) {
          var pct = (p.data && p.data.pct != null) ? p.data.pct : p.percent;
          return p.name + '\n' + pct + '%';
        },
      },
      labelLayout: { hideOverlap: true },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 700 },
        itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.18)' },
      },
      data: subs.map(function (name, i) {
        return {
          name: name,
          value: mins[i],
          // 自己算的百分比 (最大余数法, 合计正好 100)。不叫 percent ——
          // 那个字段名是 ECharts 自己算的, 覆盖了会让它跟自己打架。
          pct: pcts[i],
          itemStyle: { color: subjectColorOf(name) },
        };
      }),
    }],
  };
}

// 月内按周对比。消费月报接口返回的 weeks。
// 每周一根柱子, 柱顶标该周总用时, tooltip 里给完成率与日均。
function buildMonthWeeksOption(monthReport) {
  var weeks = (monthReport && monthReport.weeks) || [];
  if (!weeks.length) return null;

  var text2 = resolveCssColor(readToken('--text-2') || '#575046');
  var text3 = resolveCssColor(readToken('--text-3') || '#8B8377');
  var border = resolveCssColor(readToken('--border') || '#E5E0D6');
  var accent = resolveCssColor(readToken('--accent') || '#1D4E7C');
  var fontNum = readToken('--font-num') || 'sans-serif';

  var maxV = weeks.reduce(function (m, w) {
    return (w.minutes || 0) > m ? (w.minutes || 0) : m;
  }, 0);
  var step = maxV <= 50 ? 50 : maxV <= 100 ? 50 : maxV <= 200 ? 100
            : maxV <= 400 ? 200 : Math.ceil(maxV / 200) * 200;
  var niceMax = Math.max(step, Math.ceil(maxV / step) * step);

  return {
    aria: { enabled: true },
    animationDuration: 260,
    grid: { left: 44, right: 18, top: 26, bottom: 40 },
    tooltip: {
      trigger: 'axis',
      confine: true,
      formatter: function (ps) {
        var p = ps[0];
        var w = weeks[p.dataIndex] || {};
        var lines = [w.label + ' (' + (w.start || '') + ' ~ ' + (w.end || '') + ')'];
        lines.push('总用时 ' + (w.minutes || 0) + ' 分钟');
        lines.push('日均 ' + (w.avg_minutes || 0) + ' 分钟');
        lines.push('完成 ' + (w.done || 0) + ' 项' + (w.rate != null ? ' (' + w.rate + '%)' : ''));
        return lines.join('<br/>');
      },
    },
    xAxis: {
      type: 'category',
      data: weeks.map(function (w) { return w.label || ''; }),
      axisLabel: { color: text2, fontSize: 11, fontFamily: fontNum },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: border } },
    },
    yAxis: {
      type: 'value',
      name: '分钟',
      nameTextStyle: { color: text3, fontSize: 10, align: 'right' },
      max: niceMax,
      splitNumber: 2,
      axisLabel: { color: text3, fontSize: 10, fontFamily: fontNum },
      splitLine: { lineStyle: { color: border, type: 'dashed' } },
    },
    series: [{
      name: '周用时',
      type: 'bar',
      itemStyle: { color: accent },
      barMaxWidth: 56,
      label: {
        show: true,
        position: 'top',
        color: text3,
        fontSize: 10,
        fontWeight: 600,
        fontFamily: fontNum,
        formatter: function (p) { return String(p.value); },
      },
      labelLayout: { hideOverlap: true },
      data: weeks.map(function (w) { return w.minutes || 0; }),
    }],
  };
}

// ====== 渲染层 ======

// 所有 ECharts 实例的登记表。
//
// 为什么需要: 复盘页切周期时 loadReview 会 root.innerHTML = '', 但那只清了
// DOM, **不会**销毁 ECharts 实例 —— canvas 与它挂的 resize 监听都还在。
// 后果: 切 10 次周期泄漏 10 个实例; 同一 DOM 上二次 init 还会打
// "There is a chart instance already initialized on the dom" 的警告。
//
// 所以清 DOM 之前必须先 disposeCharts()。
var ChartRegistry = new Map();

// 容器尺寸变化时重画。优先 ResizeObserver (容器自身的尺寸变了也能管),
// 拿不到就退回 window.resize。
var _chartResizeObserver = null;
var _chartWindowBound = false;

function ensureResizeHook() {
  if (typeof globalThis.ResizeObserver === 'function' && !_chartResizeObserver) {
    _chartResizeObserver = new globalThis.ResizeObserver(function () {
      resizeCharts();
    });
  }
  if (!_chartWindowBound && typeof globalThis.addEventListener === 'function') {
    globalThis.addEventListener('resize', function () { resizeCharts(); });
    _chartWindowBound = true;
  }
}

function resizeCharts(registry) {
  var reg = registry || ChartRegistry;
  reg.forEach(function (inst) {
    try { inst.resize(); } catch (e) { /* 实例可能已被 dispose */ }
  });
}

// 建一张图。返回实例, 失败返回 null。
function renderChart(container, option, registry) {
  if (!container || !option) return null;
  if (!canRenderCharts()) return null;
  var reg = registry || ChartRegistry;
  // 同一个 DOM 上再建一次前先把旧的销毁 —— ECharts 只警告不抛错, 但旧实例
  // 仍在监听, 越积越多。
  if (reg.has(container)) {
    try { reg.get(container).dispose(); } catch (e) { /* 已经没了 */ }
    reg.delete(container);
  }
  var inst;
  try {
    inst = globalThis.echarts.init(container, null, { renderer: 'canvas' });
    inst.setOption(option);
  } catch (e) {
    return null;
  }
  reg.set(container, inst);
  ensureResizeHook();
  if (_chartResizeObserver) {
    try { _chartResizeObserver.observe(container); } catch (e) { /* 忽略 */ }
  }
  return inst;
}

// 销毁全部实例。清 DOM 之前必须调用。
function disposeCharts(registry) {
  var reg = registry || ChartRegistry;
  reg.forEach(function (inst, dom) {
    try { inst.dispose(); } catch (e) { /* 已经没了 */ }
    if (_chartResizeObserver) {
      try { _chartResizeObserver.unobserve(dom); } catch (e) { /* 忽略 */ }
    }
  });
  reg.clear();
}

// ====== 无 canvas 时的降级 ======
//
// jsdom (测试用) 没有 canvas 包, getContext('2d') 返回 null, 建不了图。
// 这时候把同样的数据渲染成 HTML 表格 —— 不白屏、不断言、数据还在,
// 而且表格本身就是可读的 (无障碍不靠 canvas 的 aria)。
//
// 这些函数返回 HTML 字符串, 交给 app.js 的 el() 变成 DOM。

function escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// 每日用时的表格版: 一行一天, 一列一科。
function dailyTimeTableHtml(r) {
  var days = (r && r.days) || [];
  var subs = [];
  days.forEach(function (d) {
    subjectsInOrder(d.by_subject).forEach(function (s) {
      if (subs.indexOf(s) < 0) subs.push(s);
    });
  });
  if (!days.length || !subs.length) return '';
  var head = '<tr><th>日期</th>' + subs.map(function (s) {
    return '<th>' + escHtml(s) + '</th>';
  }).join('') + '<th>合计</th></tr>';
  var body = days.map(function (d) {
    var tds = subs.map(function (s) {
      var v = (d.by_subject || {})[s] || 0;
      return '<td>' + v + '</td>';
    }).join('');
    return '<tr><td>' + escHtml(String(d.date || '').slice(5)) + '</td>' + tds
      + '<td>' + (d.total_minutes || 0) + '</td></tr>';
  }).join('');
  return '<table class="chart-fallback-table"><thead>' + head + '</thead><tbody>'
    + body + '</tbody></table>';
}

// 学科占比的表格版: 一行一科, 分钟 + 占比。
function subjectShareTableHtml(bySubject, total, mode) {
  var subs = subjectsInOrder(bySubject).filter(function (s) { return (bySubject[s] || 0) > 0; });
  if (!subs.length || !total) return '';
  var mins = subs.map(function (s) { return bySubject[s] || 0; });
  var pcts = percentsOf(mins);
  var head = '<tr><th>科目</th><th>分钟</th><th>占比</th></tr>';
  var body = subs.map(function (name, i) {
    return '<tr><td>' + escHtml(name) + '</td><td>' + mins[i] + '</td><td>'
      + pcts[i] + '%</td></tr>';
  }).join('');
  return '<table class="chart-fallback-table"><thead>' + head + '</thead><tbody>'
    + body + '</tbody></table>';
}

// 按周对比的表格版。
function monthWeeksTableHtml(monthReport) {
  var weeks = (monthReport && monthReport.weeks) || [];
  if (!weeks.length) return '';
  var head = '<tr><th>周次</th><th>区间</th><th>总用时</th><th>日均</th><th>完成</th></tr>';
  var body = weeks.map(function (w) {
    return '<tr><td>' + escHtml(w.label || '') + '</td><td>'
      + escHtml((w.start || '') + ' ~ ' + (w.end || '')) + '</td><td>'
      + (w.minutes || 0) + '</td><td>' + (w.avg_minutes || 0) + '</td><td>'
      + (w.done || 0) + ' 项</td></tr>';
  }).join('');
  return '<table class="chart-fallback-table"><thead>' + head + '</thead><tbody>'
    + body + '</tbody></table>';
}

// ====== 导出 ======
//
// 顶层不用 IIFE: app.js / dual.js 也是裸的经典脚本, 保持一致。
// 但对外挂一个 StudyCharts 对象 —— node 测试直接读它, 不用去源码里
// "抠函数"(那种抠法在函数被挪进 IIFE 后就废了)。
if (typeof globalThis !== 'undefined') {
  globalThis.StudyCharts = {
    resolveCssColor: resolveCssColor,
    readToken: readToken,
    canRenderCharts: canRenderCharts,
    buildDailyTimeOption: buildDailyTimeOption,
    buildDailyTimeNote: buildDailyTimeNote,
    buildSubjectShareOption: buildSubjectShareOption,
    buildMonthWeeksOption: buildMonthWeeksOption,
    hotDayIndexes: hotDayIndexes,
    shareOfDays: shareOfDays,
    renderChart: renderChart,
    disposeCharts: disposeCharts,
    resizeCharts: resizeCharts,
    ChartRegistry: ChartRegistry,
    dailyTimeTableHtml: dailyTimeTableHtml,
    subjectShareTableHtml: subjectShareTableHtml,
    monthWeeksTableHtml: monthWeeksTableHtml,
    SEG_LABEL_MIN_RATIO: SEG_LABEL_MIN_RATIO,
  };
}
