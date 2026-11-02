// 薄弱项文案的口径回归 (2026-11-02)
//
// 纯函数, 无需起服务: 把 dual.js 里的 renderPWeak 抠出来跑 (与
// test_chart_options.js 同一套路 —— 那边抠 charts.js, 这边抠 dual.js)。
//
// 为什么单独一个脚本: renderPWeak 在 dual.js 里, 而 dual.js 依赖 app.js 的
// 全局 (el / escapeAttr), 在 jsdom harness 里同时 eval 两个文件很容易把
// 别的断言搅乱。这里只抠这一个函数, 依赖用桩替代, 互不干扰。
//
// 守的是两句话的区别 (2026-11-02 实测踩过):
//   上周 41 条记录、偏差全为 0, 薄弱项却写"这一段还没有足够的记录" ——
//   家长会以为还欠着记录, 其实估得很准。两句话意思完全不同, 不能混用。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const dualSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'dual.js'), 'utf8');

// 抠出被测函数。按 "函数名 + 收尾的 \n}\n" 截取 —— 顶层声明, 收尾括号顶格。
function extract(name) {
  const i = dualSrc.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  const end = dualSrc.indexOf('\n}\n', i);
  if (end < 0) throw new Error(`${name} end not found`);
  return dualSrc.slice(i, end + 3);
}

// 桩: renderPWeak 只用到 el() 与 escapeAttr()。
const ctx = {
  console,
  el: (html) => ({ html, text: html.replace(/<[^>]+>/g, '') }),
  escapeAttr: (s) => String(s),
};
vm.createContext(ctx);
vm.runInContext(
  extract('renderPWeak') +
  '\nglobalThis.__exp = { renderPWeak };\n',
  ctx
);
const { renderPWeak } = ctx.__exp;

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}
function textOf(node) {
  return String(node.text || node.html || '');
}

// 样本够、偏差全为 0 → 说的是"估得准"
const accurate = textOf(renderPWeak({
  sample_size: 41,
  top_underestimate: [],
  top_overestimate: [],
}, {}));
check('样本够且无偏差时说的是"估得准"',
  accurate.includes('吻合'), true);
check('样本够且无偏差时不能说"还没有足够的记录"',
  accurate.includes('还没有足够的记录'), false);

// 样本不够 → 才是"多记几天"
const tooFew = textOf(renderPWeak({
  sample_size: 2,
  top_underestimate: [],
  top_overestimate: [],
}, {}));
check('样本不足时说的是"多记几天"',
  tooFew.includes('还没有足够的记录'), true);
check('样本不足时不能说"估得准"',
  tooFew.includes('吻合'), false);

// 有低估 → 说最该盯的那一项, 且不与上面两句混用
const under = textOf(renderPWeak({
  sample_size: 41,
  top_underestimate: [{ subject: '数学', task_type: '订正', diff_minutes: 15 }],
  top_overestimate: [],
}, {}));
check('有低估时点名科目与差值',
  under.includes('数学') && under.includes('订正') && under.includes('15'), true);
check('有低估时不说"还没有足够的记录"',
  under.includes('还没有足够的记录'), false);

// 有高估（估得保守）→ 也要说得出, 不能与"估得准"混为一谈
const over = textOf(renderPWeak({
  sample_size: 41,
  top_underestimate: [],
  top_overestimate: [{ subject: '英语', task_type: '阅读', diff_minutes: -12 }],
}, {}));
check('有高估时说明估得偏保守',
  over.includes('偏保守'), true);
check('有高估时不说"吻合"',
  over.includes('吻合'), false);

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
