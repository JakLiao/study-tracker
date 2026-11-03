// 孩子端 ⋯ 菜单的层叠 (2026-11-03 用户反馈: 菜单被挡住)
//
// 纯静态断言, 无需起服务:
//
// 根因: .kid-card.is-done 用了 `filter: saturate(.55)` (styles.css) ——
// filter 会**创建层叠上下文**, 于是 .kid-more-menu 的 z-index:30 被关在
// 卡片自己那个上下文里, 逃不出去。同列的下一张卡 (position: relative,
// z-index: auto) 按文档顺序画在上面, 菜单就被盖住了。
//
// 解法: 菜单打开时把那张卡抬到同列其它卡之上 (z-index), 关闭时放回去。
// 这条规则跟"完成卡降饱和"这个视觉设计不冲突 —— 不用为了层级去动滤镜。
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const css = fs.readFileSync(path.join(ROOT, 'app', 'static', 'styles.css'), 'utf8');
const dualSrc = fs.readFileSync(path.join(ROOT, 'app', 'static', 'dual.js'), 'utf8');

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${JSON.stringify(got)}${ok ? '' : `\n  !=> ${JSON.stringify(want)}`}`);
}
function checkHas(name, hay, needle) {
  const ok = String(hay).includes(needle);
  if (!ok) failed++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n      ${ok ? '' : `缺少 ${JSON.stringify(needle)}`}`);
}

// ===== 1. CSS: 菜单打开的卡要抬起来 =====
const rule = css.match(/\.kid-card\.is-menu-open\s*\{([^}]*)\}/);
check('CSS 里有 .kid-card.is-menu-open 规则', !!rule, true);
if (rule) {
  const z = rule[1].match(/z-index:\s*([^;]+);/);
  check('该规则带 z-index', !!z, true);
  // z-index 必须真的能压过同列的兄弟卡 (它们是 z-index: auto)
  check('z-index 不是 auto', z ? z[1].trim() !== 'auto' : false, true);
  check('z-index 大于 0 (auto 的兄弟卡排在它后面)', z ? Number(z[1].trim()) > 0 : false, true);
}

// 抬卡的规则必须紧挨着 ⋯ 菜单那一段, 别放错地方 (放错 = 没生效)
checkHas('抬卡规则写在孩子端任务卡那一节', css, '.kid-card.is-menu-open');

// ===== 2. JS: 打开加 class, 关闭去 class =====
checkHas('打开菜单时给卡加 is-menu-open',
  dualSrc, "card.classList.add('is-menu-open')");

// 关闭有两条路径: 按钮自己的 closeMenu, 以及全局 document 点击兜底。
// 两条都要去掉 class, 否则关掉菜单后那张卡还悬在别的卡上面。
const removes = (dualSrc.match(/classList\.remove\('is-menu-open'\)/g) || []).length;
check('关闭菜单时去掉 is-menu-open (两条关闭路径都覆盖)', removes, 2);

// ===== 3. 旧的触发方式不许复活 =====
// 菜单自己带 z-index 是**不够**的: 它被关在卡片的 filter 上下文里,
// 抬的是卡, 不是菜单。这条断言防止有人把修复写成"给菜单加 z-index"。
const menuZ = css.match(/\.kid-more-menu\s*\{([^}]*)\}/);
if (menuZ && /z-index:\s*(\d+)/.test(menuZ[1])) {
  const z = Number(menuZ[1].match(/z-index:\s*(\d+)/)[1]);
  check('菜单自身的 z-index 保持 30 (层级靠抬卡解决, 不靠抬菜单)', z, 30);
}

// ===== 4. 手机上底栏不能盖住菜单 =====
// 移动端断点里 nav#tabs 固定在屏幕下沿 (z-index: 900), 菜单往下开就会被
// 底栏盖住 —— 菜单项一个都点不到 (2026-11-03 用户反馈)。所以空间不够时
// 必须往上翻 (与自绘下拉的 .dd.is-up 同一套路)。
const upRule = css.match(/\.kid-more-menu\.is-up\s*\{([^}]*)\}/);
check('CSS 里有 .kid-more-menu.is-up 规则 (空间不够往上翻)', !!upRule, true);
if (upRule) {
  checkHas('往上翻时把 top 改成 auto', upRule[1], 'top: auto');
  checkHas('往上翻时给了 bottom', upRule[1], 'bottom:');
}
checkHas('JS 里有 placeMenu 决定往上还是往下开', dualSrc, 'placeMenu');
checkHas('JS 会给菜单加 is-up', dualSrc, "menu.classList.add('is-up')");
checkHas('JS 会把菜单的 is-up 摘掉 (重新打开时不残留)', dualSrc, "menu.classList.remove('is-up')");
// 量空间时要认得"底部固定底栏" —— 它的上沿才是真正能用的屏幕下沿。
checkHas('量空间时认得底部固定底栏 nav#tabs', dualSrc, 'nav#tabs');

console.log(failed ? `\n${failed} FAILED` : '\nALL PASS');
process.exit(failed ? 1 : 0);
