// 各科剩余时间 (2026-10-09) 口径回归
// 纯函数, 无需起服务: 把 app.js 里的 SUBJECT_ORDER / isSettledItem /
// subjectTimeStats 抠出来跑。与 test_board_hint_regression.js 同套路。
//
// 这个功能真正要守住的是"两处数字对得上账": 各科剩余之和必须等于提示条
// 那个 estRemaining。两者在界面上紧挨着, 一旦算法漂移, 家长会看到
// "语文 20/50 + 数学 30/40 = 50" 却写着"大约还要 70 分钟", 直接失去信任。
// 所以下面的用例不只测 subjectTimeStats 本身, 还把它和提示条的算法
// 放在一起做交叉验证。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'app', 'static', 'app.js'), 'utf8');

// 抠出被测代码: 从 SUBJECT_ORDER 到 subjectTimeStats 函数结束。
// 用显式的锚点而不是"下一个 }\n", 因为 isSettledItem 夹在中间,
// 按花括号截会把它切掉。
const start = src.indexOf('const SUBJECT_ORDER');
const endMark = src.indexOf('\n}\n', src.indexOf('function subjectTimeStats('));
if (start < 0 || endMark < 0) throw new Error('subjectTimeStats not found');
const ctx = {};
vm.createContext(ctx);
// 末尾补一句显式导出: vm 里只有 var 和函数声明会挂到 context 对象上,
// const 进的是词法环境, ctx.SUBJECT_ORDER 会是 undefined。
// 补这行把三个符号统一导出, 测试里就不必关心这个差别。
vm.runInContext(
  src.slice(start, endMark + 3) +
  '\nglobalThis.__exp = { subjectTimeStats, isSettledItem, SUBJECT_ORDER };\n',
  ctx
);
const { subjectTimeStats, isSettledItem, SUBJECT_ORDER } = ctx.__exp;

// 提示条用的那个 estRemaining 算法, 从 loadBoard 里原样抠出来。
// 这样测的就真是界面上跑的那份, 而不是测试里另写一遍。
const estMatch = src.match(/const estRemaining = pending\.reduce\([^;]+;/);
if (!estMatch) throw new Error('estRemaining not found in loadBoard');
const estRemainingOf = vm.runInNewContext(
  `(function(pending){ ${estMatch[0].replace('const estRemaining =', 'return')} })`
);

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}

// 构造条目。status 默认 todo —— 后端 est_minutes 在看板上是非空 int,
// 这里仍允许显式传 null, 因为 subjectTimeStats 里的 `|| 0` 是防御性的。
const it = (subject, est, status = 'todo') => ({ subject, est_minutes: est, status });

// ===== 基本口径: 剩余 / 总量 =====
check('全部未做 => 剩余等于总量',
  subjectTimeStats({
    语文: [it('语文', 15), it('语文', 10)],
    数学: [it('数学', 25)],
  }),
  [
    { subject: '语文', remaining: 25, total: 25 },
    { subject: '数学', remaining: 25, total: 25 },
  ]);

check('部分完成 => 剩余只算未收尾',
  subjectTimeStats({
    语文: [it('语文', 15), it('语文', 10, 'done'), it('语文', 20)],
  }),
  [{ subject: '语文', remaining: 35, total: 45 }]);

// skipped 与 done 同等算收尾 —— 跳过不该让数字一直挂在那里,
// 否则孩子点完"今天不做"看到时间没降, 会以为按钮没生效。
check('skipped 同样算收尾',
  subjectTimeStats({
    数学: [it('数学', 10, 'skipped'), it('数学', 5)],
  }),
  [{ subject: '数学', remaining: 5, total: 15 }]);

check('doing 仍算未完成',
  subjectTimeStats({
    英语: [it('英语', 12, 'doing')],
  }),
  [{ subject: '英语', remaining: 12, total: 12 }]);

check('整科做完后 => remaining 0 但 total 保留',
  subjectTimeStats({
    语文: [it('语文', 15, 'done'), it('语文', 10, 'done')],
  }),
  [{ subject: '语文', remaining: 0, total: 25 }]);

// ===== 空态: 不硬凑 "0 / 0" =====
check('当天完全没作业 => 空数组',
  subjectTimeStats({ 语文: [], 数学: [], 英语: [] }),
  []);

check('by_subject 缺 key => 不报错, 也没有该科',
  subjectTimeStats({ 数学: [it('数学', 10)] }),
  [{ subject: '数学', remaining: 10, total: 10 }]);

check('by_subject 整体为 undefined => 空数组而非抛错',
  subjectTimeStats(undefined),
  []);

check('某科 est 全为 null => 该科不出现(无法估时就不占位置)',
  subjectTimeStats({ 英语: [it('英语', null), it('英语', null)] }),
  []);

// ===== 顺序: 必须与 SUBJECT_ORDER 一致 =====
// 顺序变了孩子会以为数据在跳, 这里钉死 语文 -> 数学 -> 英语。
check('输出顺序固定为 语文/数学/英语',
  subjectTimeStats({
    英语: [it('英语', 10)],
    数学: [it('数学', 10)],
    语文: [it('语文', 10)],
  }).map(s => s.subject),
  ['语文', '数学', '英语']);

check('SUBJECT_ORDER 与后端 config.SUBJECTS 一致',
  SUBJECT_ORDER,
  ['语文', '数学', '英语']);

// ===== 与提示条交叉验证: 两处数字必须对得上 =====
// 这是本测试最关键的一条。各科剩余之和 === 提示条 estRemaining。
const board = {
  语文: [it('语文', 15), it('语文', 10, 'done'), it('语文', 20)],
  数学: [it('数学', 25), it('数学', 10, 'skipped')],
  英语: [it('英语', 15)],
};
const all = [];
for (const s of SUBJECT_ORDER) all.push(...(board[s] || []));
const pending = all.filter(i => !isSettledItem(i));
const fromHint = estRemainingOf(pending);
const fromSubjects = subjectTimeStats(board).reduce((s, x) => s + x.remaining, 0);
check('各科剩余之和 === 提示条 estRemaining',
  fromSubjects,
  fromHint);

// 反向也钉一次: 三科全做完时两边必须同时归零。
const allDone = {
  语文: [it('语文', 15, 'done')],
  数学: [it('数学', 25, 'skipped')],
  英语: [it('英语', 15, 'done')],
};
const doneAll = [];
for (const s of SUBJECT_ORDER) doneAll.push(...(allDone[s] || []));
check('全部收尾 => 两处同时为 0',
  [
    subjectTimeStats(allDone).reduce((s, x) => s + x.remaining, 0),
    estRemainingOf(doneAll.filter(i => !isSettledItem(i))),
  ],
  [0, 0]);

// ===== 结构契约 =====
// loadBoard 靠 total>0 的过滤结果决定要不要渲染这一行, 并靠字段名
// 拼 HTML。这里三个字段任一改名, 界面就会显示 NaN 或空白。
const first = subjectTimeStats({ 语文: [it('语文', 10)] })[0];
check('返回字段为 subject/remaining/total',
  Object.keys(first).sort(),
  ['remaining', 'subject', 'total']);

// ===== 界面接线: 渲染代码不能被摘掉 =====
const renderable = src
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
for (const [label, pattern] of [
  ['loadBoard 未调用 subjectTimeStats', /subjectTimeStats\(b\.by_subject\)/],
  ['未渲染剩余/总量两个数', /<strong>\$\{s\.remaining\}<\/strong>/],
]) {
  if (!pattern.test(renderable)) {
    console.log(`FAIL  ${label}: ${pattern}`);
    failed++;
  } else {
    console.log(`PASS  ${label}`);
  }
}

// 硬编码的科目字面量曾在 loadBoard 里出现过一次, 现在统一走
// SUBJECT_ORDER。再写一份就意味着两处顺序可能不一致。
if (/for \(const subj of \['语文'/.test(renderable)) {
  console.log('FAIL  loadBoard 里仍有硬编码科目字面量');
  failed++;
} else {
  console.log('PASS  科目顺序统一走 SUBJECT_ORDER');
}

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
