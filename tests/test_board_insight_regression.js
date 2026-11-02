// 家长端「一句人话结论」回归 (2026-11-02)
//
// 修掉的 bug: 概览卡下面那句结论渲染成
//   "还有 [object Object],[object Object],... 项没做完, 大约还要 60 分钟。"
//
// 根因: buildBoardInsight 的 pending 契约是"项数"(数字), 但调用处把
// 整个任务数组传了进去。模板插值时数组被 toString(), 每个元素变成
// "[object Object]", 逗号一连就是一串。
// 附带一个同源的坑: pending 是空数组时 `!pending` 为 false (空数组是
// 真值), "全部完成"那句永远不会触发, 会掉到 "还有  项没做完"。
//
// 这里同时守住两层:
//  1. 函数层: 无论收到数组还是数字, 都输出项数, 不出现 [object Object]
//  2. 调用层: 调用处必须传 pending.length, 不能把数组直接传进来
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');
const start = src.indexOf('function buildBoardInsight(');
const end = src.indexOf('\n}\n', start) + 3;
if (start < 0) throw new Error('buildBoardInsight not found');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(src.slice(start, end), ctx);
const buildBoardInsight = ctx.buildBoardInsight;

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${got}${ok ? '' : `\n  !=> ${want}`}`);
}

// ===== 正常口径: pending 传项数 =====
check('pending=5 传项数',
  buildBoardInsight({ pct: 0, pending: 5, estRemaining: 60, estTotal: 60, actTotal: 0 }),
  '还有 5 项没做完, 大约还要 60 分钟。');

// ===== 回归: pending 传了任务数组 =====
const items = [
  { id: 1, title: '订正限时计算' },
  { id: 2, title: '校对第三单元达标练习答案' },
  { id: 3, title: '完成知能P35-36-37' },
  { id: 4, title: '完成英语手册P23-24' },
  { id: 5, title: '背诵英语书本P38-39' },
];
const fromArray = buildBoardInsight({ pct: 0, pending: items, estRemaining: 60, estTotal: 60, actTotal: 0 });
check('pending=任务数组 => 不能出现 [object Object]', fromArray.includes('[object Object]'), false);
check('pending=任务数组 => 输出项数 5',
  fromArray,
  '还有 5 项没做完, 大约还要 60 分钟。');

// ===== 空数组: "全部完成"那句必须能触发 =====
// 空数组是 JS 真值, `!pending` 判不出"没有待做"。
check('pending=空数组 => 全部完成',
  buildBoardInsight({ pct: 100, pending: [], estRemaining: 0, estTotal: 60, actTotal: 0 }),
  '今天的必做全部完成, 收工。');
check('pending=0 => 全部完成',
  buildBoardInsight({ pct: 100, pending: 0, estRemaining: 0, estTotal: 60, actTotal: 0 }),
  '今天的必做全部完成, 收工。');

// ===== 实际用时对比分支不受影响 =====
// 注意: "全部完成, 收工"优先于用时对比, 所以对比分支只在还有待做项时才走到。
check('实际比预估少',
  buildBoardInsight({ pct: 50, pending: 1, estRemaining: 20, estTotal: 60, actTotal: 40 }),
  '实际用时比预估少 20 分钟, 效率不错。');
check('实际比预估多',
  buildBoardInsight({ pct: 50, pending: 1, estRemaining: 20, estTotal: 60, actTotal: 80 }),
  '实际用时比预估多 20 分钟, 今天偏慢一些。');
check('实际与预估一致',
  buildBoardInsight({ pct: 50, pending: 1, estRemaining: 20, estTotal: 60, actTotal: 60 }),
  '实际用时和预估一样, 节奏稳。');
check('有待做项但没有实际用时 => 报剩余项数',
  buildBoardInsight({ pct: 50, pending: 3, estRemaining: 20, estTotal: 60, actTotal: 0 }),
  '还有 3 项没做完, 大约还要 20 分钟。');

// ===== 调用层: 必须传 pending.length =====
const callSite = 'buildBoardInsight({ pct, pending: pending.length, estRemaining, estTotal, actTotal })';
check('调用处传 pending.length', src.includes(callSite), true);

if (failed) {
  console.log(`\n${failed} 条断言失败`);
  process.exit(1);
}
console.log('\n全部通过');
