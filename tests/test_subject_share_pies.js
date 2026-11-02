// 各科时间占比环图 (2026-11-02 由手写 SVG 改为 ECharts) 口径回归
// 纯函数, 无需起服务: 把 app.js 的 sharePercents 与 charts.js 的
// buildSubjectShareOption 抠出来跑。与 test_subject_time_stats.js 同套路。
//
// 这块真正要守住的是三件事:
//   1. 百分比加起来必须正好 100% —— 各自四舍五入会得到 33%+33%+33%=99%,
//      家长看到会以为图错了;
//   2. 科目顺序必须固定 (语文→数学→英语) —— Object.entries 的键序来自
//      入库顺序, 每天不一样, 家长没法把颜色对应到科目;
//   3. 总量为 0 时**不能**产出一个空环 —— 一个只有底色的圆会被误读成
//      "占比为 0"。
//
// 2026-11-02 之前这里测的是 piePoint / pieSlice / pieLabelTier 这套手写
// SVG 的几何与标签分档逻辑。换成 ECharts 之后:
//   - pieSlice / piePoint 没有了 (扇形由 ECharts 画);
//   - pieLabelTier 的"放不下就退一档"改由 ECharts 的
//     labelLayout.hideOverlap 接管 —— 所以断言换成"hideOverlap 开着"。
// 保护的**意图**没变, 只是断言的对象换了。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');
const chartsSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'charts.js'), 'utf8');

// 抠出被测函数。按 "函数名 + 收尾的 \n}\n" 截取 —— 这几个函数都是
// 顶层声明, 收尾括号顶格, 内部的缩进括号不会被误伤。
function extract(src, name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  const end = src.indexOf('\n}\n', i);
  if (end < 0) throw new Error(`${name} end not found`);
  return src.slice(i, end + 3);
}

// 抠出常量声明 (const 不会挂到 context 上, 但同一段脚本里的函数能用)。
// 前缀匹配一次抠全部 —— 少抠一条就会在运行时 ReferenceError。
function extractConsts(src, prefix) {
  const found = src.match(new RegExp(`^const ${prefix}[^\\n]*$`, 'gm'));
  if (!found || !found.length) throw new Error(`${prefix} not found`);
  return found.join('\n') + '\n';
}

// charts.js 里的纯函数要用到 app.js 的三个全局 (科目色 / 科目顺序 /
// 百分比取整)。这里注入**等价实现** —— 与页面真实行为一致:
//   subjectColor  -> 固定色值 (画布不认 CSS 变量)
//   orderedSubjects -> SUBJECT_ORDER 的顺序
//   sharePercents -> 真的从 app.js 抠出来 (它是被测对象之一)
const ctx = {
  console,
  // getComputedColor 在 node 里不存在 -> resolveCssColor 会退回原值。
  // 注入一个假的 getComputedStyle, 让色值能解析出来, 断言才看得到真色。
  getComputedStyle: () => ({
    getPropertyValue: (name) => ({
      '--c-语': '#BF3B2E', '--c-数': '#2C6BA8', '--c-英': '#457315',
      '--text-1': '#16130F', '--text-2': '#575046', '--text-3': '#8B8377',
      '--border': '#E5E0D6', '--surface-2': '#FAF9F6', '--accent': '#1D4E7C',
      '--st-late-c': '#C2321F', '--font-num': 'sans-serif',
    }[name] || ''),
  }),
  document: { documentElement: {}, createElement: () => ({ getContext: () => null }) },
};
vm.createContext(ctx);

// app.js 的 sharePercents —— 真实现, 抠出来跑。
vm.runInContext(extract(appSrc, 'sharePercents'), ctx);
vm.runInContext(
  'globalThis.subjectColor = (n) => ({ 语文: "var(--c-语)", 数学: "var(--c-数)", 英语: "var(--c-英)" }[n] || "");\n' +
  'globalThis.orderedSubjects = (b) => ["语文", "数学", "英语"].filter(s => (b || {})[s] != null);\n',
  ctx
);
// charts.js 的被测函数。
vm.runInContext(
  extract(chartsSrc, 'resolveCssColor') +
  extract(chartsSrc, 'readToken') +
  extract(chartsSrc, 'subjectColorOf') +
  extract(chartsSrc, 'subjectsInOrder') +
  extract(chartsSrc, 'percentsOf') +
  extract(chartsSrc, 'buildSubjectShareOption') +
  '\nglobalThis.__exp = { resolveCssColor, percentsOf, buildSubjectShareOption, subjectColorOf };\n',
  ctx
);
const { resolveCssColor, percentsOf, buildSubjectShareOption } = ctx.__exp;

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}

// ===== 百分比: 必须正好 100 =====
check('整数占比原样返回',
  percentsOf([40, 35, 25]),
  [40, 35, 25]);

check('三等分 => 34/33/33, 合计 100',
  percentsOf([1, 1, 1]),
  [34, 33, 33]);

check('本来就能整除时原样保留',
  percentsOf([33, 33, 34]),
  [33, 33, 34]);

check('余数补给小数部分最大的那一项',
  percentsOf([20, 30, 50]),
  [20, 30, 50]);

check('两科各半 => 50/50',
  percentsOf([10, 10]),
  [50, 50]);

check('单科占满 => 100%',
  percentsOf([25]),
  [100]);

check('全零 => 全零 (不抛 NaN)',
  percentsOf([0, 0]),
  [0, 0]);

check('空数组 => 空数组',
  percentsOf([]),
  []);

// 合计恒等于 100 是这张图最容易失守的性质, 逐组验一遍。
const cases = [[1, 2, 3], [7, 11, 13, 17], [1, 1, 1, 1, 1], [999, 1], [5, 5, 5, 5, 5, 5, 5, 5, 5, 5]];
for (const c of cases) {
  const sum = percentsOf(c).reduce((s, v) => s + v, 0);
  check(`合计 100%: ${JSON.stringify(c)}`, sum, 100);
}

// ===== CSS 变量必须解成实际色值 =====
// 画布不认 var(--x), 解不出来就等于把颜色丢了。
check("var(--c-语) 解成实际色值",
  resolveCssColor('var(--c-语)'),
  '#BF3B2E');

check("普通色值原样返回",
  resolveCssColor('#123456'),
  '#123456');

// ===== 环图 option =====
const opt = buildSubjectShareOption({ 语文: 60, 数学: 30, 英语: 10 }, 100, 'actual');
const s = opt.series[0];

check('环图类型', s.type, 'pie');
check('是环 (内外半径都给了)', s.radius, ['58%', '82%']);

// 科目顺序固定: 故意倒着给 by_subject, 输出必须还是 语文→数学→英语。
const optRev = buildSubjectShareOption({ 英语: 10, 数学: 30, 语文: 60 }, 100, 'actual');
check('扇形顺序固定 (语→数→英), 不受 by_subject 键序影响',
  optRev.series[0].data.map(d => d.name),
  ['语文', '数学', '英语']);

check('扇形数值与占比',
  s.data.map(d => [d.name, d.value, d.pct]),
  [['语文', 60, 60], ['数学', 30, 30], ['英语', 10, 10]]);

// 标签必须直接标在扇形上 (不用在图例和色块之间来回对照)
check('扇形内显示标签', s.label.show, true);
check('标签落在扇形里面', s.label.position, 'inside');
// 放不下时由 ECharts 自动藏掉, 而不是把字挤到相邻扇形上
check('标签避让交给 hideOverlap', s.labelLayout.hideOverlap, true);

// 无障碍: ECharts 内置 aria, 颜色不是唯一信息载体
check('开启 aria', opt.aria.enabled, true);

check('模式名进 series 名 (预估)',
  buildSubjectShareOption({ 语文: 10 }, 10, 'est').series[0].name,
  '预计时间占比');
check('模式名进 series 名 (实际)',
  buildSubjectShareOption({ 语文: 10 }, 10, 'actual').series[0].name,
  '实际时间占比');

// 总量为 0 / 没有科目 => 不产出环 (调用方据此显示空态)
check('总量为 0 时不产出租形图',
  buildSubjectShareOption({ 语文: 0 }, 0, 'actual'),
  null);
check('没有科目时不产出租形图',
  buildSubjectShareOption({}, 100, 'actual'),
  null);

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
