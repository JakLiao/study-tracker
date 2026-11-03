// 孩子端文案的历史日期口径 (2026-11-03 用户反馈)
//
// 纯函数, 无需起服务: 把 dual.js 里的 kidDayWord / kidHint / renderKidHero
// 抠出来跑 (与 test_weak_item.js 同一套路)。
//
// 这次钉的是「同类问题」: 孩子端看板能翻历史日期, 但文案里的"今天"是
// 写死的 —— 点「昨天」, 顶上写"今天的作业全部完成啦!"、罐子画的是今天
// 拿到的 15 颗星、按钮叫"今天不做"。对着昨天的作业喊今天, 等于没翻页。
//
// 口径参照家长端已有的做法 (app.js 的 boardHint):
//   isToday ? '今天' : '这天'
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

const ctx = {
  console,
  el: (html) => ({ html, setAttribute() {} }),
  escapeAttr: (s) => String(s),
};
vm.createContext(ctx);
vm.runInContext(
  'const JAR_CAP = 20;\nconst JAR_VISIBLE = 9;\n'
  + extract('kidDayWord')
  + extract('kidHint')
  + extract('kidStarSeed')
  + extract('starPath')
  + extract('renderJarStars')
  + extract('renderKidHero')
  + '\nglobalThis.__exp = { kidDayWord, kidHint, renderKidHero };\n',
  ctx
);
const { kidDayWord, kidHint, renderKidHero } = ctx.__exp;

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}
function checkHas(name, hay, needle) {
  const ok = String(hay).includes(needle);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(String(hay).slice(0, 160))}${ok ? '' : `\n  !=> 应包含 ${JSON.stringify(needle)}`}`);
}

// ===== 1. "今天 / 这天" =====
check('看今天 => "今天"', kidDayWord(true), '今天');
check('看历史日期 => "这天"', kidDayWord(false), '这天');
check('不传参数时默认"今天"', kidDayWord(), '今天');

// ===== 2. 鼓励语四档, 都要跟着日期走 =====
// 第 2 档 ("还差 N 项") 本来就没有"今天"两个字, 不该被改坏。
check('今天 + 全做完 => "今天的作业全部完成啦！"',
  kidHint(0, true), '今天的作业全部完成啦！');
check('历史日期 + 全做完 => "这天的作业全部完成啦！"',
  kidHint(0, false), '这天的作业全部完成啦！');

check('今天 + 剩 2 项',
  kidHint(2, true), '还差 2 项就全部完成啦');
check('历史日期 + 剩 2 项 (这档本来就没有"今天")',
  kidHint(2, false), '还差 2 项就全部完成啦');

check('今天 + 剩 3 项',
  kidHint(3, true), '今天作业有点多哦，慢慢来');
check('历史日期 + 剩 3 项',
  kidHint(3, false), '这天作业有点多哦，慢慢来');

check('今天 + 剩 6 项',
  kidHint(6, true), '今天有点多，先做最想先做的那项');
check('历史日期 + 剩 6 项',
  kidHint(6, false), '这天有点多，先做最想先做的那项');

// 不传 isToday 的老调用 (默认参数) 不能炸, 也不该改口。
check('不传 isToday => 仍说"今天"',
  kidHint(0), '今天的作业全部完成啦！');

// ===== 3. 星星罐 hero =====
// stars 走 loadKidStars(date): 翻历史日期时, 后端返回的是那天的
// today_earned / jar / 连续天数, 所以 hero 只要把"今天"换成"这天"。
const histStars = { today_earned: 12, balance: 32, streak: { current: 7 } };
const todayStars = { today_earned: 15, balance: 32, streak: { current: 8 } };

const heroToday = renderKidHero({ remaining: 0, stars: todayStars, doneCount: 11, isToday: true });
checkHas('今天: 副标题说"今天拿到"', heroToday.html, '今天拿到');
checkHas('今天: 罐子里画的是今天拿到的 15 颗星 (--stars:15)', heroToday.html, '--stars:15');
checkHas('今天: 连续天数按今天算 (8 天)', heroToday.html, '已经连续 8 天啦');

const heroHist = renderKidHero({ remaining: 0, stars: histStars, doneCount: 7, isToday: false });
checkHas('历史日期: 副标题说"这天拿到"', heroHist.html, '这天拿到');
checkHas('历史日期: 不许出现"今天拿到"', heroHist.html.replace(/今天拿到/g, ''), '这天拿到');
check('历史日期: 副标题里不该有"今天拿到"', /今天拿到/.test(heroHist.html), false);
check('历史日期: 罐子画的是那天的 12 颗星 (--stars:12)', heroHist.html.includes('--stars:12'), true);
check('历史日期: 连续天数截止那天 (7 天)', heroHist.html.includes('已经连续 7 天啦'), true);
check('历史日期: "罐里有" 仍是当前余额 (32 颗)', heroHist.html.includes('罐里有 <b>32</b> 颗'), true);

// ===== 4. 界面接线 =====
const renderable = dualSrc
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:])\/\/[^\n]*/g, '$1');

for (const [label, pattern] of [
  // 星星按看板看的那一天取 —— 后端 /kid/stars?date= 就是为这个准备的
  ['星星按看板日期取', /loadKidStars\(b\.date\)/],
  // hero / 任务卡都要拿到 isToday
  ['hero 收到 isToday', /renderKidHero\(\{[^}]*isToday: isToday/s],
  ['任务卡收到 isToday', /renderKidCard\(item, refresh, isToday\)/],
  // 空状态也不能说"今天"
  ['空状态按日期分文案', /isToday \? '今天没有作业/],
]) {
  if (!pattern.test(renderable)) {
    console.log(`FAIL  ${label}: ${pattern}`);
    failed++;
  } else {
    console.log(`PASS  ${label}`);
  }
}

// 旧写法: 星星永远取今天 (不带日期) —— 必须消失, 否则翻历史日期还是今天的数。
if (/loadKidStars\(\)\s*;/.test(renderable) && !/loadKidStars\(b\.date\)/.test(renderable)) {
  console.log('FAIL  看板没有按日期取星星');
  failed++;
} else {
  console.log('PASS  看板按日期取星星');
}

// 任务卡上的「今天不做」也要跟着日期走。
if (!/const day = kidDayWord\(isToday\)/.test(renderable)
    || !/const skipText = `\$\{day\}不做`/.test(renderable)) {
  console.log('FAIL  任务卡的「今天不做」没有按日期分文案');
  failed++;
} else {
  console.log('PASS  任务卡的「今天不做」按日期分文案');
}

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
