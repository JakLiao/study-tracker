// 时长格式化 (2026-11-03): 分钟 → 「49小时16分钟」
//
// 用户看到月报卡上的「作业分钟 2956」, 提的要求是"应该显示 xx 小时 xx
// 分钟" —— 四位数读不出分量, 换成小时+分钟才知道那快 50 小时了。
//
// 这套纯函数不碰 DOM, 直接抠出来跑 (同 test_subject_time_stats.js 套路)。
//
// 要守住的性质:
//   1. 不足一小时**不换算** —— "0小时45分钟"比"45分钟"更难读;
//   2. 整小时不拖一个 "0分钟" (50 小时, 不是 50小时0分钟);
//   3. 负数/空值不炸 —— 卡片里那个值来自后端, 缺字段时是 undefined;
//   4. 日均这类小数保留一位小数 (62.5), 不显示成 62.50000001。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const appSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');

// 抠出被测函数。按 "函数名 + 收尾的 \n}\n" 截取 —— 这几个函数都是
// 顶层声明, 收尾括号顶格, 内部的缩进括号不会被误伤。
function extract(src, name) {
  const i = src.indexOf(`function ${name}(`);
  if (i < 0) throw new Error(`${name} not found`);
  const end = src.indexOf('\n}\n', i);
  if (end < 0) throw new Error(`${name} end not found`);
  return src.slice(i, end + 3);
}

const ctx = { console };
vm.createContext(ctx);
vm.runInContext(
  extract(appSrc, 'durationParts') +
  extract(appSrc, 'formatDuration') +
  extract(appSrc, 'durationStatHtml') +
  '\nglobalThis.__exp = { durationParts, formatDuration, durationStatHtml };\n',
  ctx
);
const { durationParts, formatDuration, durationStatHtml } = ctx.__exp;

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}

// ===== 换算 =====
check('2956 分钟 => 49小时16分钟',
  formatDuration(2956), '49小时16分钟');

check('整小时不带 0分钟',
  formatDuration(3000), '50小时');

check('刚好一小时',
  formatDuration(60), '1小时');

check('59 分钟不换算',
  formatDuration(59), '59分钟');

check('0 分钟',
  formatDuration(0), '0分钟');

// ===== 边界 =====
check('负数按 0 算 (后端缺字段时不炸)',
  formatDuration(-5), '0分钟');

check('null 按 0 算',
  formatDuration(null), '0分钟');

check('undefined 按 0 算',
  formatDuration(undefined), '0分钟');

check('非数字按 0 算',
  formatDuration('abc'), '0分钟');

check('不足一小时的小数保留一位 (45.5)',
  formatDuration(45.5), '45.5分钟');

check('过了 60 就换算成小时 (62.5 分 => 1小时3分钟)',
  formatDuration(62.5), '1小时3分钟');

check('四舍五入到分 (90.6 => 91)',
  formatDuration(90.6), '1小时31分钟');

// ===== 分开的两部分 (环图中心用) =====
check('大数字 + 小单位',
  durationParts(2956), { big: '49', unit: '小时16分钟' });

check('不足一小时时大数字就是分钟数',
  durationParts(45), { big: '45', unit: '分钟' });

// ===== 卡片里的字号 =====
check('长时长降一档字号',
  durationStatHtml(2956), '<strong class="is-dur">49小时16分钟</strong>');

check('短时长不降字号 (别比同行小一号)',
  durationStatHtml(45), '<strong>45分钟</strong>');

if (failed) {
  console.log(`\n${failed} 条失败`);
  process.exit(1);
}
console.log('\nALL PASS');
