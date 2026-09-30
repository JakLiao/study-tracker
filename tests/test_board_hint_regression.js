// boardHint 提示语分档回归 (2026-09-29)
// 纯函数, 无需起服务: 把 app.js 里的 boardHint 抠出来跑各档位。
// 关注两件事:
//  1. 语气是否随剩余量递进(剩 1 项最接近终点, 文案最冲刺)
//  2. 历史日期不能自称"今天" —— 看板可翻过去的日子
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');
const start = src.indexOf('function boardHint(');
const end = src.indexOf('\n}\n', start) + 3;
if (start < 0) throw new Error('boardHint not found');
const ctx = {};
vm.createContext(ctx);
vm.runInContext(src.slice(start, end), ctx);
const boardHint = ctx.boardHint;

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${got}${ok ? '' : `\n  !=> ${want}`}`);
}

// ===== 分档: 未完成态 =====
check('剩 1 项(今天, 带估时)',
  boardHint({ isToday: true, pending: 1, done: 5, skipped: 0, estRemaining: 15 }).html,
  '就差最后 <strong>1</strong> 项啦, 大约还要 <strong>15</strong> 分钟, 慢慢完成就好 🌿');

check('剩 1 项(无估时 => 不出现 "0 分钟")',
  boardHint({ isToday: true, pending: 1, done: 5, skipped: 0, estRemaining: 0 }).html,
  '就差最后 <strong>1</strong> 项啦, 慢慢完成就好 🌿');

check('剩 2 项 => almost 档',
  boardHint({ isToday: true, pending: 2, done: 3, skipped: 0, estRemaining: 40 }).html,
  '还剩 <strong>2</strong> 项, 大约还要 <strong>40</strong> 分钟, 加油! ✨');

check('剩 3 项 => 仍是 almost 档(边界含 3)',
  boardHint({ isToday: true, pending: 3, done: 2, skipped: 0, estRemaining: 60 }).html,
  '还剩 <strong>3</strong> 项, 大约还要 <strong>60</strong> 分钟, 加油! ✨');

check('剩 4 项 => 落到 many 档(边界不含 3)',
  boardHint({ isToday: true, pending: 4, done: 1, skipped: 0, estRemaining: 90 }).html,
  '今天还有 <strong>4</strong> 项, 大约还要 <strong>90</strong> 分钟, 一件一件来');

check('剩 8 项 => many 档',
  boardHint({ isToday: true, pending: 8, done: 0, skipped: 0, estRemaining: 200 }).html,
  '今天还有 <strong>8</strong> 项, 大约还要 <strong>200</strong> 分钟, 一件一件来');

// ===== 分档: 收尾态 =====
check('全部真做完 => 庆祝 + 绿色',
  boardHint({ isToday: true, pending: 0, done: 5, skipped: 0, estRemaining: 0 }).html,
  '今天的必做全部完成, 太棒了! 🎉');

check('全靠跳过收尾(done=0) => 不庆祝, 降为灰色',
  boardHint({ isToday: true, pending: 0, done: 0, skipped: 5, estRemaining: 0 }).html,
  '今天的必做都处理完啦 ✓');

check('混合(done=3, skipped=2) => 按真做完庆祝',
  boardHint({ isToday: true, pending: 0, done: 3, skipped: 2, estRemaining: 0 }).html,
  '今天的必做全部完成, 太棒了! 🎉');

// ===== 历史日期不得自称"今天" =====
check('历史日期 剩 1 项 => 不出现"今天"',
  boardHint({ isToday: false, pending: 1, done: 5, skipped: 0, estRemaining: 15 }).html,
  '就差最后 <strong>1</strong> 项啦, 大约还要 <strong>15</strong> 分钟, 慢慢完成就好 🌿');

check('历史日期 many 档 => 用"这天"',
  boardHint({ isToday: false, pending: 4, done: 1, skipped: 0, estRemaining: 90 }).html,
  '这天还有 <strong>4</strong> 项, 大约还要 <strong>90</strong> 分钟, 一件一件来');

check('历史日期 收尾 => 用"这天"',
  boardHint({ isToday: false, pending: 0, done: 5, skipped: 0, estRemaining: 0 }).html,
  '这天的必做全部完成, 太棒了! 🎉');

// ===== cls 必须四档互不重叠 =====
const cl = n => boardHint({ isToday: true, pending: n, done: 1, skipped: 0, estRemaining: 1 }).cls;
check('cls: 剩 1 项', cl(1), 'last-one');
check('cls: 剩 3 项', cl(3), 'almost');
check('cls: 剩 4 项', cl(4), 'many');
check('cls: 全完成', boardHint({ isToday: true, pending: 0, done: 2 }).cls, 'all-clear');
check('cls: 全跳过降档', boardHint({ isToday: true, pending: 0, done: 0, skipped: 2 }).cls, 'all-clear all-clear-dim');

// ===== 旧文案不得残留在 app.js =====
if (/今日还有 <strong>\$\{pending\.length\}<\/strong> 项要做/.test(src)) {
  console.log('FAIL  旧提示语仍残留在 app.js');
  failed++;
} else {
  console.log('PASS  旧提示语已从 app.js 移除');
}

// 2026-09-29 用户反馈: "冲一把"对小朋友太冲。语气档的措辞属于产品决定,
// 不该被后来的改动悄悄换掉 —— 这里钉住具体措辞, 换词必须同步改本测试。
// 同步检查 💪: 肌肉 emoji 与"冲一把"是同一套鼓劲信号, 只改文案不改图标
// 会让语气悄悄退回催促感。
// 先剥掉注释再匹配: 上面的决策记录里就写着被否掉的措辞, 而注释不渲染
// 给用户。检查不到界面文案才是失败, 注释里复盘旧写法不算。
const renderable = src
  .replace(/\/\*[\s\S]*?\*\//g, '')   // 块注释
  .replace(/(^|[^:])\/\/[^\n]*/g, '$1'); // 行注释(避开 http://)
for (const [label, pattern] of [
  ['催促式措辞', /冲一把|加把劲|赶紧|提速|别磨蹭|一口气/],
  ['催促式 emoji', /💪/],
]) {
  if (pattern.test(renderable)) {
    console.log(`FAIL  ${label}仍出现在 app.js: ${pattern}`);
    failed++;
  } else {
    console.log(`PASS  ${label}已从 app.js 界面文案中清除`);
  }
}

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
