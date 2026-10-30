// 各科时间占比饼图 (2026-10-29) 口径回归
// 纯函数, 无需起服务: 把 app.js 里的 piePoint / pieSlice / sharePercents
// 抠出来跑。与 test_subject_time_stats.js 同套路。
//
// 这块真正要守住的是两件事:
//   1. 百分比加起来必须正好 100% —— 各自四舍五入会得到 33%+33%+33%=99%,
//      家长看到会以为图错了;
//   2. 单科目占满 100% 时整块饼不能消失 —— SVG 的 A 命令在起止点重合
//      时画出来是一条退化直线。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');

// 抠出被测函数。按 "函数名 + 收尾的 \n}\n" 截取 —— 这几个函数都是
// 顶层声明, 收尾括号顶格, 内部的缩进括号不会被误伤。
function extract(name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  const end = src.indexOf('\n}\n', i);
  if (end < 0) throw new Error(`${name} end not found`);
  return src.slice(i, end + 3);
}

// 抠出常量声明 (const 不会挂到 context 上, 但同一段脚本里的函数能用)。
// 前缀匹配一次抠全部 —— PIE_* 有好几条, 少抠一条就会在运行时 ReferenceError。
function extractConsts(prefix) {
  const found = src.match(new RegExp(`^const ${prefix}[^\\n]*$`, 'gm'));
  if (!found || !found.length) throw new Error(`${prefix} not found`);
  return found.join('\n') + '\n';
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(
  extractConsts('PIE') + extract('piePoint') + extract('pieSlice') +
  extract('pieLabelHalfAngle') + extract('pieLabelTier') + extract('sharePercents') +
  '\nglobalThis.__exp = { piePoint, pieSlice, pieLabelTier, sharePercents };\n',
  ctx
);
const { piePoint, pieSlice, pieLabelTier, sharePercents } = ctx.__exp;

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}

// ===== 百分比: 必须正好 100 =====
check('整数占比原样返回',
  sharePercents([40, 35, 25]),
  [40, 35, 25]);

check('三等分 => 34/33/33, 合计 100',
  sharePercents([1, 1, 1]),
  [34, 33, 33]);

check('本来就能整除时原样保留',
  sharePercents([33, 33, 34]),
  [33, 33, 34]);

check('余数补给小数部分最大的那一项',
  sharePercents([20, 30, 50]),
  [20, 30, 50]);

check('两科各半 => 50/50',
  sharePercents([10, 10]),
  [50, 50]);

check('单科占满 => 100%',
  sharePercents([25]),
  [100]);

check('全零 => 全零 (不抛 NaN)',
  sharePercents([0, 0]),
  [0, 0]);

check('空数组 => 空数组',
  sharePercents([]),
  []);

// 合计恒等于 100 是这张图最容易失守的的性质, 逐组验一遍。
const cases = [[1, 2, 3], [7, 11, 13, 17], [1, 1, 1, 1, 1], [999, 1], [5, 5, 5, 5, 5, 5, 5, 5, 5, 5]];
for (const c of cases) {
  const sum = sharePercents(c).reduce((s, v) => s + v, 0);
  check(`合计 100%: ${JSON.stringify(c)}`, sum, 100);
}

// ===== 扇形上放不放得下标签 =====
// 科目名要直接标在扇形上, 但文字横排着, 窄扇形会被相邻扇形压掉。
// 判据是"文字在标签半径上张开的角度"不能超过扇形的半张角。
// 返回 0 = 放不下 (只留图例), 1 = 只放科目名, 2 = 科目名 + 百分比。

check('三科均分 => 名字和百分比都放得下',
  [pieLabelTier(1 / 3), pieLabelTier(0.34), pieLabelTier(0.39)],
  [2, 2, 2]);

check('常见的失衡分布 (45/35/20) 三块都放得下名字和百分比',
  [pieLabelTier(0.45), pieLabelTier(0.35), pieLabelTier(0.20)],
  [2, 2, 2]);

check('再窄一点 (18%) 只放得下科目名, 百分比让给图例',
  pieLabelTier(0.18),
  1);

check('极窄的扇形放不下, 交给图例',
  [pieLabelTier(0.10), pieLabelTier(0.05), pieLabelTier(0.01)],
  [0, 0, 0]);

check('整圆 (单科目 100%) 两行都放得下',
  pieLabelTier(1),
  2);

check('超过半圈时两行都放得下 (它已经包住大半张图)',
  [pieLabelTier(0.6), pieLabelTier(0.9)],
  [2, 2]);

check('0 占比放不下',
  pieLabelTier(0),
  0);

// ===== 扇形路径 =====
const full = pieSlice(95, 95, 68, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2, '#000', 'x');
check('整圆用 circle 画 (A 命令画不出整圆)',
  full.startsWith('<circle') && full.includes('r="68"'),
  true);

const half = pieSlice(95, 95, 68, -Math.PI / 2, Math.PI / 2, '#000', 'x');
check('半圆用 path + 弧线命令',
  half.startsWith('<path') && half.includes(' A 68 68 '),
  true);

check('半圆 largeArc=0',
  half.includes('A 68 68 0 0 1'),
  true);

const threeQuarters = pieSlice(95, 95, 68, -Math.PI / 2, -Math.PI / 2 + Math.PI * 1.5, '#000', 'x');
check('超过半圈 largeArc=1',
  threeQuarters.includes('A 68 68 0 1 1'),
  true);

// 起点固定在 12 点方向: 圆心正上方。
const [sx, sy] = piePoint(95, 95, 68, -Math.PI / 2);
check('扇形起点在 12 点方向',
  [Math.abs(sx - 95) < 0.001, Math.abs(sy - (95 - 68)) < 0.001],
  [true, true]);

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
