// 孩子端庆祝层的触发门槛 (2026-11-03 用户反馈)
//
// 纯函数, 无需起服务: 把 dual.js 里的 shouldCelebrate 抠出来跑
// (与 test_weak_item.js 同一套路 —— 那边抠 renderPWeak, 这边抠庆祝门槛)。
//
// 这次钉的是 2026-11-03 用户反馈的两件事:
//   1) 点「昨天」看历史, 不该弹「今天全部完成啦!」
//      —— 弹窗文案写死"今天", 翻到昨天也弹, 等于对着昨天的作业喊今天;
//         而且星星数取的是**今天**的 today_earned, 数字也是错的。
//   2) 这个框经常弹 —— 去重只存在内存变量里, 刷新一次就忘, 于是每次
//      打开页面都弹一遍。得在浏览器里记住"这个日期已经庆祝过了"。
//
// 门槛函数必须是纯的: 时间戳、localStorage 的读写都留在调用方,
// 这样断言不用起浏览器、不用起服务。
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

const ctx = { console };
vm.createContext(ctx);
vm.runInContext(
  extract('shouldCelebrate') + '\nglobalThis.__exp = { shouldCelebrate };\n',
  ctx
);
const { shouldCelebrate } = ctx.__exp;

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}

const TODAY = '2026-11-03';
const YESTERDAY = '2026-11-02';

// ===== 1. 只看今天 =====
check('今天 + 全部做完 + 没记录过 => 弹',
  shouldCelebrate({ isToday: true, allDone: true, date: TODAY,
    celebratedDate: null, savedDate: null }), true);

// 翻到昨天: 全做完了也不弹。这是本次反馈的第一条。
check('昨天 + 全部做完 => 不弹',
  shouldCelebrate({ isToday: false, allDone: true, date: YESTERDAY,
    celebratedDate: null, savedDate: null }), false);

check('历史日期 + 全部做完 + 那天记录过 => 不弹',
  shouldCelebrate({ isToday: false, allDone: true, date: YESTERDAY,
    celebratedDate: YESTERDAY, savedDate: YESTERDAY }), false);

// ===== 2. 真的全做完才弹 =====
check('今天 + 还有待做 => 不弹',
  shouldCelebrate({ isToday: true, allDone: false, date: TODAY,
    celebratedDate: null, savedDate: null }), false);

check('今天 + 一条作业都没有 => 不弹',
  shouldCelebrate({ isToday: true, allDone: false, date: TODAY,
    celebratedDate: null, savedDate: null }), false);

// ===== 3. 同一个浏览器同一天只弹一次 =====
// 内存里已经弹过 (这次会话内切了别的标签又切回来) => 不弹
check('今天 + 本次会话已弹过 => 不弹',
  shouldCelebrate({ isToday: true, allDone: true, date: TODAY,
    celebratedDate: TODAY, savedDate: null }), false);

// 浏览器里记着今天庆祝过 (刷新过页面 / 换设备后再打开) => 不弹
check('今天 + 浏览器已记录今天 => 不弹',
  shouldCelebrate({ isToday: true, allDone: true, date: TODAY,
    celebratedDate: null, savedDate: TODAY }), false);

// 记的是昨天 —— 那是新的一天, 该弹
check('今天 + 浏览器记的是昨天 => 弹',
  shouldCelebrate({ isToday: true, allDone: true, date: TODAY,
    celebratedDate: null, savedDate: YESTERDAY }), true);

// 记的是别的日期 (比如上上周) —— 不该挡住今天
check('今天 + 浏览器记的是别的日期 => 弹',
  shouldCelebrate({ isToday: true, allDone: true, date: TODAY,
    celebratedDate: null, savedDate: '2026-10-20' }), true);

// ===== 4. 界面接线 =====
// 只查渲染代码, 不查注释 —— 注释里必然会写到这些词, 查不到界面
// 行为才是失败, 注释里复盘旧写法不算。
const renderable = dualSrc
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:])\/\/[^\n]*/g, '$1');

for (const [label, pattern] of [
  ['看板用 shouldCelebrate 决定是否弹', /if \(shouldCelebrate\(/],
  ['弹之前把日期记进浏览器', /writeCelebrateRecord\(b\.date\)/],
  ['旧的无 is_today 判断的写法已删掉', null],
]) {
  if (pattern === null) continue;
  if (!pattern.test(renderable)) {
    console.log(`FAIL  ${label}: ${pattern}`);
    failed++;
  } else {
    console.log(`PASS  ${label}`);
  }
}

// 旧写法 "_celebratedFor !== b.date" 只按日期去重, 不看是不是今天 ——
// 它必须整个消失, 否则哪天又被改回来就又弹了。
if (/doneAll\s*&&\s*_celebratedFor\s*!==\s*b\.date/.test(renderable)) {
  console.log('FAIL  旧写法 doneAll && _celebratedFor !== b.date 还在');
  failed++;
} else {
  console.log('PASS  旧写法 doneAll && _celebratedFor !== b.date 已删掉');
}

// 弹窗本身必须在门槛之后调用, 不能无条件调。
// (函数定义本身也含 "showCelebrate(", 计数前先把它摘掉。)
const callSites = renderable.replace(/function showCelebrate\([^)]*\)\s*\{/, '')
  .match(/showCelebrate\(/g) || [];
check('showCelebrate 只在门槛之后被调用一次', callSites.length, 1);

// 庆祝记录必须落在 localStorage 里 (刷新一次就忘 = 用户反馈的"经常弹")。
if (!/localStorage\.setItem\([^)]*CELEBRATE/i.test(renderable)
    && !/localStorage\.setItem\(\s*CELEBRATE_STORE_KEY/.test(renderable)) {
  console.log('FAIL  庆祝记录没有写进 localStorage');
  failed++;
} else {
  console.log('PASS  庆祝记录写进了 localStorage');
}

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
