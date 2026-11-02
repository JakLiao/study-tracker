// 图表 option 的口径回归 (2026-11-02)
//
// 纯函数, 无需起服务: 把 charts.js 里的 option 构造函数抠出来跑。
// 与 test_subject_share_pies.js 同一套路 (那边测环图, 这边测柱状图与按周图)。
//
// 这些 option 决定家长看到什么, 值得钉住:
//   1. 柱数跟着实际天数走 (月视图三十来天, 不能写死 7);
//   2. 科目顺序固定 (语→数→英) —— 家长按颜色找科目;
//   3. 不上学的日子**单独一个 series**, 与"用时 0"分开画;
//   4. 段内数字只在占比够大时才写, 不然会压到相邻段上;
//   5. 密集时要有 dataZoom —— 三十来天挤一屏根本看不清。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const chartsSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'charts.js'), 'utf8');

// 抠出被测函数。按 "函数名 + 收尾的 \n}\n" 截取 —— 这些函数都是顶层声明,
// 收尾括号顶格, 内部的缩进括号不会被误伤。
function extract(name) {
  const i = chartsSrc.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  const end = chartsSrc.indexOf('\n}\n', i);
  if (end < 0) throw new Error(`${name} end not found`);
  return chartsSrc.slice(i, end + 3);
}

const TOKENS = {
  '--c-语': '#BF3B2E', '--c-数': '#2C6BA8', '--c-英': '#457315',
  '--text-1': '#16130F', '--text-2': '#575046', '--text-3': '#8B8377',
  '--border': '#E5E0D6', '--surface-2': '#FAF9F6', '--accent': '#1D4E7C',
  '--st-late-c': '#C2321F', '--font-num': 'sans-serif',
};

const ctx = {
  console,
  getComputedStyle: () => ({ getPropertyValue: (n) => TOKENS[n] || '' }),
  document: { documentElement: {}, createElement: () => ({ getContext: () => null }) },
};
vm.createContext(ctx);
// 段内标签的门槛是一个顶层 var, 抠函数时得一起抠出来 —— 否则 formatter
// 运行时会 ReferenceError。
const segDecl = chartsSrc.match(/^var SEG_LABEL_MIN_RATIO\s*=.*$/m);
if (!segDecl) throw new Error('SEG_LABEL_MIN_RATIO not found');
vm.runInContext(
  'globalThis.subjectColor = (n) => ({ 语文: "var(--c-语)", 数学: "var(--c-数)", 英语: "var(--c-英)" }[n] || "");\n' +
  'globalThis.orderedSubjects = (b) => ["语文", "数学", "英语"].filter(s => (b || {})[s] != null);\n' +
  'globalThis.sharePercents = (mins) => { const t = mins.reduce((a,b)=>a+b,0); if(!t) return mins.map(()=>0);' +
  ' const ex = mins.map(m => m/t*100); const out = ex.map(Math.floor); let rest = 100 - out.reduce((a,b)=>a+b,0);' +
  ' const byRem = ex.map((x,i)=>({i,rem:x-Math.floor(x)})).sort((a,b)=>b.rem-a.rem);' +
  ' for(let k=0;rest>0;k++,rest--) out[byRem[k%byRem.length].i]+=1; return out; };\n',
  ctx
);
vm.runInContext(
  segDecl[0] + '\n' +
  extract('resolveCssColor') +
  extract('readToken') +
  extract('subjectColorOf') +
  extract('subjectsInOrder') +
  extract('percentsOf') +
  extract('shareOfDays') +
  extract('hotDayIndexes') +
  extract('buildDailyTimeNote') +
  extract('buildDailyTimeOption') +
  extract('buildMonthWeeksOption') +
  '\nglobalThis.__exp = { buildDailyTimeOption, buildDailyTimeNote, buildMonthWeeksOption, hotDayIndexes, shareOfDays };\n',
  ctx
);
const { buildDailyTimeOption, buildDailyTimeNote, buildMonthWeeksOption, hotDayIndexes, shareOfDays } = ctx.__exp;

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}

// 一周的七天: 周一~周五有作业, 周六周日是"不上学的普通休息日"。
const DAYS = [
  { date: '2026-10-19', weekday_label: '周一', kind: 'plain', is_school_day: true,
    by_subject: { '语文': 55 }, total_minutes: 55 },
  { date: '2026-10-20', weekday_label: '周二', kind: 'plain', is_school_day: true,
    by_subject: { '数学': 40 }, total_minutes: 40 },
  { date: '2026-10-21', weekday_label: '周三', kind: 'friday', is_school_day: true,
    by_subject: { '语文': 50 }, total_minutes: 50 },
  { date: '2026-10-22', weekday_label: '周四', kind: 'makeup', is_school_day: true,
    by_subject: { '英语': 60 }, total_minutes: 60 },
  { date: '2026-10-23', weekday_label: '周五', kind: 'plain', is_school_day: true,
    by_subject: { '语文': 30 }, total_minutes: 30 },
  { date: '2026-10-24', weekday_label: '周六', kind: 'off', is_school_day: false,
    by_subject: {}, total_minutes: 0 },
  { date: '2026-10-25', weekday_label: '周日', kind: 'off', is_school_day: false,
    by_subject: {}, total_minutes: 0 },
];

const STATS = {
  period: 'current', period_start: '2026-10-19', period_end: '2026-10-25',
  days: DAYS,
  plain_avg: { days: 3, by_subject: { '语文': 28.3 }, total_minutes: 41.7 },
  school_day_count: 5,
  excluded_days: [],
};

// ====== 每日用时柱状图 ======
const opt = buildDailyTimeOption(STATS, {});
const xdates = opt.xAxis.data;

check('x 轴是实际的天数 (不写死 7)',
  xdates.length, 7);
check('x 轴日期取到日',
  xdates, ['10-19', '10-20', '10-21', '10-22', '10-23', '10-24', '10-25']);

// 科目顺序固定: 故意让 by_subject 的键序是 英->数->语
const optRev = buildDailyTimeOption({
  ...STATS,
  days: [{ ...DAYS[0], by_subject: { '英语': 10, '数学': 20, '语文': 30 }, total_minutes: 60 }],
}, {});
check('堆叠顺序固定 (语→数→英), 不受 by_subject 键序影响',
  optRev.series.filter(s => s.name !== '不上学').map(s => s.name),
  ['语文', '数学', '英语']);

check('每个科目一个堆叠 series',
  opt.series.filter(s => s.name !== '不上学').map(s => s.stack),
  ['total', 'total', 'total']);

// 不上学的日子单独一个 series —— 与"用时 0"分开
const offSeries = opt.series.find(s => s.name === '不上学');
check('不上学的日子单独一个 series', !!offSeries, true);
check('不上学的日子不进学科堆叠', offSeries.stack, 'off');
check('不上学的日子是虚线描边',
  offSeries.itemStyle.borderType, 'dashed');
check('不上学的日子只有周末那两天有值',
  offSeries.data.filter(v => v != null).length, 2);

// 上学日的 series 在不上学的日子上必须是 0 —— 不占高度, 不画出可见的柱。
// "这天的标记"由「不上学」那个 series 承担 (虚线小方块)。
const yuwen = opt.series.find(s => s.name === '语文');
check('不上学的日子在学科 series 里不占高度',
  yuwen.data.slice(5), [0, 0]);

// 段内标签: 占比太小不写数字
check('段内标签按比例阈值决定',
  typeof yuwen.label.formatter, 'function');
// 当天共 55 分钟: 1 分钟的段占 1.8% < 6% -> 不写字 (塞进去会压到相邻段上)
check('占比太小的段不写字',
  yuwen.label.formatter({ dataIndex: 0, value: 1 }, yuwen.data[0]), '');
check('占比够大的段写字',
  yuwen.label.formatter({ dataIndex: 0, value: 55 }, yuwen.data[0]), '55');

check('标签避让交给 hideOverlap', yuwen.labelLayout.hideOverlap, true);
check('开启 aria', opt.aria.enabled, true);

// ====== 密集视图 (月视图) ======
const many = [];
for (let i = 1; i <= 31; i++) {
  many.push({
    date: `2026-11-${String(i).padStart(2, '0')}`,
    weekday_label: '周' + i,
    kind: 'plain', is_school_day: true,
    by_subject: { '语文': 20 }, total_minutes: 20,
  });
}
const optMonth = buildDailyTimeOption({ ...STATS, period: 'month', days: many }, {});
check('月视图 31 天全画', optMonth.xAxis.data.length, 31);
check('月视图有缩放把手', !!optMonth.dataZoom, true);
check('月视图日期标签让 ECharts 自己挑间隔',
  optMonth.xAxis.axisLabel.interval, 'auto');
check('周视图不加缩放把手 (一周 7 天不需要)', !!opt.dataZoom, false);

// ====== 异常高点 ======
check('中位数 1.5 倍以上算异常',
  hotDayIndexes([
    { total_minutes: 100 }, { total_minutes: 100 },
    { total_minutes: 100 }, { total_minutes: 300 },
  ]), [3]);
check('没有异常点时返回空',
  hotDayIndexes([{ total_minutes: 100 }, { total_minutes: 110 }]), []);
check('全零时返回空 (中位数是 0, 不能拿 0 当门槛)',
  hotDayIndexes([{ total_minutes: 0 }, { total_minutes: 0 }]), []);

const note = buildDailyTimeNote({
  days: [
    { weekday_label: '周一', total_minutes: 100 },
    { weekday_label: '周二', total_minutes: 100 },
    { weekday_label: '周三', total_minutes: 300 },
  ],
});
check('说明文字点名异常的那天', note.includes('周三'), true);
check('说明文字带中位数', note.includes('中位数 100'), true);

const calmNote = buildDailyTimeNote({
  days: [
    { weekday_label: '周一', total_minutes: 100 },
    { weekday_label: '周二', total_minutes: 110 },
  ],
});
check('没有异常点时改说最高的那天', calmNote.includes('最高的一天'), true);
check('没有异常点时不能说"明显高于平时"',
  calmNote.includes('明显高于平时'), false);

// ====== 占比与柱状图对账 (shareOfDays) ======
//
// 「显示周末」开关把周末那两天从逐日柱状图里藏掉, 但周末可能真有作业
// (2026-11-02 实测: 10/31 与 11/01 各 10 分钟语文)。占比环如果还按整段
// 算, 同一屏上柱状图 685、环图 705 —— 家长只会以为图画错了。
// 所以图上用的占比必须由"看得见的那几天"重算。
const SHARE_DAYS = [
  { date: '2026-10-26', kind: 'plain', is_school_day: true,
    by_subject: { '语文': 40 }, est_by_subject: { '语文': 60 }, total_minutes: 40 },
  { date: '2026-10-30', kind: 'friday', is_school_day: true,
    by_subject: { '语文': 105, '数学': 45 }, est_by_subject: { '语文': 110, '数学': 45 }, total_minutes: 150 },
  { date: '2026-10-31', kind: 'off', is_school_day: false,
    by_subject: { '语文': 10 }, est_by_subject: { '语文': 10 }, total_minutes: 10 },
];

const visible = SHARE_DAYS.filter(d => d.kind !== 'off');
const share = shareOfDays(visible);
check('实际合计 = 看得见那几天的逐日合计',
  share.actual.total_minutes, 190);
check('实际按科目拆开',
  share.actual.by_subject, { '语文': 145, '数学': 45 });
check('预估合计 = 看得见那几天的逐日预估',
  share.est.total_minutes, 215);
check('预估按科目拆开',
  share.est.by_subject, { '语文': 170, '数学': 45 });
check('周末那 10 分钟不进这个环 (整段是 200)',
  shareOfDays(SHARE_DAYS).actual.total_minutes, 200);
check('没有逐日预估时返回 null (调用方保留整段合计)',
  shareOfDays([{ date: '2026-10-26', by_subject: { '语文': 40 }, total_minutes: 40 }]), null);
check('空数组返回 null', shareOfDays([]), null);

// ====== 月内按周对比 ======
const monthOpt = buildMonthWeeksOption({
  weeks: [
    { label: '第1周', start: '2026-11-01', end: '2026-11-07', minutes: 150, avg_minutes: 21.4, done: 12, required: 13, rate: 92 },
    { label: '第2周', start: '2026-11-08', end: '2026-11-14', minutes: 300, avg_minutes: 42.9, done: 20, required: 20, rate: 100 },
  ],
});
check('按周图的 x 轴是周次', monthOpt.xAxis.data, ['第1周', '第2周']);
check('按周图的柱值是各周用时', monthOpt.series[0].data, [150, 300]);
check('按周图开了 aria', monthOpt.aria.enabled, true);
check('没有周次时不产出一张空图',
  buildMonthWeeksOption({ weeks: [] }), null);

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
