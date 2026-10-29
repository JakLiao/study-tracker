// 庆祝横幅 + 选做折叠 (2026-10-29 用户反馈)
// 纯函数, 无需起服务: 把 app.js 里的 optionalGroupOpen / hintBlock /
// boardHint 抠出来跑。与 test_board_hint_regression.js 同套路。
//
// 这次改的是两处"看得见的取舍", 都很容易被后来的改版悄悄改回去:
//  1. 夸奖从概览卡里一行小字升级成独立横幅 (hintBlock)
//  2. 选做全做完后折叠成一行 (optionalGroupOpen)
// 两条都是"看起来能省地方"的选择, 但省错了地方家长就找不到信息,
// 所以各钉一组断言。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}

// ===== 抠出被测函数 =====
// optionalGroupOpen: 独立函数, 直接抠。
const oStart = src.indexOf('function optionalGroupOpen(');
const oEnd = src.indexOf('\n}\n', oStart) + 3;
if (oStart < 0 || oEnd < 3) throw new Error('optionalGroupOpen not found');

// hintBlock: 依赖 el(), 用桩替掉 —— 只关心它选了哪个分支与输出什么。
// 桩返回 { html } 而不是真 DOM, 因为本测试要验的是"渲染哪一块",
// 而不是 DOM 结构 (那是 pytest + jsdom 那套的活)。
const hStart = src.indexOf('function hintBlock(');
const hEnd = src.indexOf('\n}\n', hStart) + 3;
if (hStart < 0 || hEnd < 3) throw new Error('hintBlock not found');

const ctx = { el: html => ({ html }) };
vm.createContext(ctx);
vm.runInContext(src.slice(oStart, oEnd) + src.slice(hStart, hEnd), ctx);
const { optionalGroupOpen, hintBlock } = ctx;

const opt = status => ({ status });
const D = 'done', T = 'todo', S = 'skipped';

// ===== 1. 选做分组: 什么时候展开 =====
// 规则 (2026-10-29): 还有没做完的选做 -> 展开; 全做完 -> 折叠成一行。
check('全是 todo => 展开',
  optionalGroupOpen([opt(T), opt(T)]), true);
check('全部做完 => 折叠',
  optionalGroupOpen([opt(D), opt(D)]), false);
check('做完一条还剩一条 => 展开',
  optionalGroupOpen([opt(D), opt(T)]), true);
check('空数组 => 折叠(没有可展开的内容)',
  optionalGroupOpen([]), false);
// 跳过的选做仍可撤销, 折叠起来家长就点不到"撤销跳过"了。
check('含跳过 => 仍展开',
  optionalGroupOpen([opt(D), opt(S)]), true);
check('单条做完 => 折叠',
  optionalGroupOpen([opt(D)]), false);

// ===== 2. 夸奖: 什么时候用横幅 =====
// celebrate=true 只在"真做完"时成立: 全靠跳过收尾的那天不弹横幅。
const hint = (o) => ({ cls: o.cls || 'x', html: o.html || 'H', celebrate: !!o.celebrate });

check('庆祝态 => 渲染 celebrate-banner',
  hintBlock(hint({ cls: 'all-clear', html: '太棒了', celebrate: true })).html,
  '<div class="celebrate-banner">太棒了</div>');

check('非庆祝态 => 仍渲染卡内 board-hint',
  hintBlock(hint({ cls: 'almost', html: '还剩 2 项' })).html,
  '<div class="board-hint almost">还剩 2 项</div>');

check('全跳过降档态 => 不弹横幅',
  hintBlock(hint({ cls: 'all-clear all-clear-dim', html: '都处理完啦' })).html,
  '<div class="board-hint all-clear all-clear-dim">都处理完啦</div>');

check('无提示 => 不渲染任何元素',
  hintBlock(null), null);

// ===== 3. 界面接线 =====
// 只查渲染代码, 不查注释 —— 注释里必然会写到这些词, 查不到界面
// 行为才是失败, 注释里复盘旧写法不算。
const renderable = src
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:])\/\/[^\n]*/g, '$1');

for (const [label, pattern] of [
  ['看板未调用 optionalGroupCard', /optionalGroupCard\(b\.optional_items/],
  ['日历详情未调用 optionalGroupCard', /optionalGroupCard\(optional,/],
  ['看板未调用 hintBlock', /hintBlock\(hint\)/],
  ['庆祝态未从概览卡里移除重复提示', /hint && !hint\.celebrate/],
]) {
  if (!pattern.test(renderable)) {
    console.log(`FAIL  ${label}: ${pattern}`);
    failed++;
  } else {
    console.log(`PASS  ${label}`);
  }
}

// 选做分组只能有一处 details —— 两处各写一份, 展开规则迟早不一致。
const detailsCount = (renderable.match(/<details class="card optional-card"/g) || []).length;
check('选做分组的 details 只有一处', detailsCount, 1);

// 庆祝横幅只能有一处 —— 两处各写一份, 配色与文案会漂。
const bannerCount = (renderable.match(/celebrate-banner/g) || []).length;
check('celebrate-banner 只出现一次', bannerCount, 1);

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
