// ====== 双角色前端 (2026-11-01) ======
//
// 孩子端与家长端的新视图逻辑, 全部放在这个独立文件里。
//
// 为什么不写在 app.js 里: app.js 有一批被**逐字钉着**的既有函数
// (tests/ 下的四个 node 脚本与若干 pytest 直接断言它们的输出与出现位置),
// 新写的东西跟它们混在一个文件里, 改新代码时很容易一脚踩到旧断言。
// 拆成两个文件后, "只增不改"的边界就清楚了: app.js 里只留**接线**,
// 具体渲染都在这里。
//
// 这里可以直接用 app.js 的全局函数 (api / el / escapeAttr / switchTab /
// renderAssignment / confirmDialog / openEditAssignmentModal ...):
// 两个都是普通脚本, 共享同一个全局词法环境, 且 app.js 先加载。
//
// 角色相关的约定:
//   孩子端 —— 星星罐 + 大任务卡 + 贴纸册 + 成绩单; 口径见规范 §4.1~§4.4
//   家长端 —— 批改台 (数据卡 + 密集行 + 批量条); 见规范 §4.5~§4.6
// 文案口径见规范 §6: 孩子端不出现"已收尾/必做/选做/复盘"这些成人词。

// ====== 孩子端文案 (规范 §6.1) ======
//
// 孩子端文案里的"今天 / 这天"。
//
// 看板能翻历史日期 —— 点「昨天」时说"今天"是错的, 孩子会以为根本没翻页。
// 口径与家长端一致 (app.js 的 boardHint): isToday ? '今天' : '这天'。
// (2026-11-03 用户反馈)
function kidDayWord(isToday = true) {
  return isToday ? '今天' : '这天';
}

// 鼓励语按"还剩几项"分四档。写死一句不如分档: 剩 1 项时喊"加油"是
// 催, 剩 8 项时喊"就差最后一项"是骗人。
//
// isToday 决定"今天 / 这天"这两个字 (翻历史日期时不能说"今天")。
function kidHint(remaining, isToday = true) {
  const day = kidDayWord(isToday);
  if (remaining <= 0) return `${day}的作业全部完成啦！`;
  if (remaining <= 2) return `还差 ${remaining} 项就全部完成啦`;
  if (remaining <= 5) return `${day}作业有点多哦，慢慢来`;
  return `${day}有点多，先做最想先做的那项`;
}

// 孩子端的"任务类型"说法。培训打卡 → 练习打卡 (规范 §6.1)。
function kidSubjectText(item) {
  if (item.subject) return item.subject;
  const kind = item.task_kind || 'subject';
  if (item.project_name) return item.project_name;
  return kind === 'training' ? '练习' : kind === 'habit' ? '习惯' : '作业';
}

// 学科色条的类名 (与既有 .subject-tag.<科目> 同一套色)。
function kidSubjectClass(item) {
  if (item.subject) return item.subject;
  const kind = item.task_kind || 'subject';
  return kind === 'training' ? 'training' : kind === 'habit' ? 'habit' : 'subject';
}

// ====== 星星 (规范 §4.1 / §7.1) ======
//
// 星星的**发放**在后端 (完成作业自动发星), 前端只读。
// 所以这里必须能扛住"后端还没上线": 拉取失败就让罐子空着, 页面照常渲染。
let _kidStars = null;

// date: 看哪一天就取哪天的星星 (后端 /kid/stars?date= 返回的是那一天
// 的 today_earned / jar / 连续天数)。不传 = 今天 (成绩单页用的是这个)。
// 字段名 today_earned 是接口约定, 值按请求的日期算 —— 别被名字骗了。
async function loadKidStars(date) {
  try {
    const q = date ? `?date=${encodeURIComponent(date)}` : '';
    const r = await api('GET', `/kid/stars${q}`);
    _kidStars = r && typeof r === 'object' ? r : null;
  } catch (e) {
    _kidStars = null;          // 接口没上线: 空罐, 不报错
  }
  return _kidStars;
}

// 固定种子。星星位置必须由序号派生, 不能用 Math.random ——
// 否则每次重绘星星都会跳位, 孩子会以为罐子里的东西在乱动。
function kidStarSeed(i, salt) {
  let x = ((i + 1) * 374761393 + (salt + 1) * 668265263) >>> 0;
  x = ((x ^ (x >>> 13)) >>> 0) * 1274126177 >>> 0;
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}

// 五角星路径 (外接圆半径 R, 内接圆半径 r = 0.382R)。
function starPath(cx, cy, R) {
  const r = R * 0.382;
  const pts = [];
  for (let i = 0; i < 10; i++) {
    const rad = (Math.PI / 5) * i - Math.PI / 2;
    const rr = i % 2 === 0 ? R : r;
    pts.push(`${(cx + rr * Math.cos(rad)).toFixed(1)} ${(cy + rr * Math.sin(rad)).toFixed(1)}`);
  }
  return `M ${pts.join(' L ')} Z`;
}

const JAR_CAP = 20;         // 罐子容量 (颗), 满了显示溢出数
const JAR_VISIBLE = 9;      // 罐里最多画几颗, 多了会挤成一团

// 罐子里的星星。--stars 传给 CSS 决定罐子填充高度。
function renderJarStars(count) {
  const shown = Math.min(count, JAR_VISIBLE);
  const colors = ['var(--star)', 'var(--star-2)', 'var(--star-3)'];
  let g = '';
  for (let i = 0; i < shown; i++) {
    const row = Math.floor(i / 3);
    const col = i % 3;
    // 位置: 三列往上堆, 每颗再用固定种子做一点点偏移, 免得排成方阵
    const jx = (kidStarSeed(i, 1) - 0.5) * 12;
    const jy = (kidStarSeed(i, 2) - 0.5) * 8;
    const x = 38 + col * 24 + jx;
    const y = 112 - row * 20 - jy;
    const fill = colors[Math.floor(kidStarSeed(i, 3) * 3) % 3];
    g += `<path d="${starPath(x, y, 8.5)}" fill="${fill}" opacity="0.95"/>`;
  }
  return g;
}

// 星星罐 hero (规范 §4.1 的结构)。
//
// isToday 决定文案里的"今天 / 这天"; stars 是按**看板看的那一天**取来的
// (loadKidStars(date)), 所以翻到历史日期时, 罐子画的是那天的星星,
// 连续天数也截止那天 (后端 /kid/stars?date= 就是这么算的)。
function renderKidHero({ remaining, stars, doneCount, isToday = true }) {
  const dayStars = stars ? (stars.today_earned || 0) : 0;
  const balance = stars ? (stars.balance || 0) : 0;
  const jarStars = Math.min(dayStars, JAR_CAP);
  const overflow = dayStars - jarStars;
  const streak = stars && stars.streak ? stars.streak.current : 0;
  const day = kidDayWord(isToday);
  const hero = el(`
    <div class="kid-hero">
      <div class="jar${dayStars >= JAR_CAP ? ' is-full' : ''}" aria-hidden="true">
        <svg class="jar-svg" viewBox="0 0 120 140" focusable="false">
          <path class="jar-neck" d="M44 12h32v14H44z" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>
          <path class="jar-glass" d="M40 26h40c4 0 7 3 7 7v78c0 9-7 16-16 16H49c-9 0-16-7-16-16V33c0-4 3-7 7-7z"
                fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>
          <g class="jar-stars">${renderJarStars(jarStars)}</g>
        </svg>
        <div class="jar-fill" style="--stars:${jarStars}"></div>
        ${overflow > 0 ? `<span class="jar-over">+${overflow}</span>` : ''}
      </div>
      <div class="kid-hero__text">
        <p class="kid-hero__line">${escapeAttr(kidHint(remaining, isToday))}</p>
        <p class="kid-hero__sub">${day}拿到 <b>${dayStars}</b> 颗星 · 罐里有 <b>${balance}</b> 颗</p>
        <p class="kid-hero__streak">${streak > 0 ? `已经连续 ${streak} 天啦` : ''}</p>
      </div>
    </div>
  `);
  hero.setAttribute('aria-label',
    `${kidHint(remaining, isToday)} ${day}拿到 ${dayStars} 颗星, 罐里有 ${balance} 颗, 完成 ${doneCount} 项`);
  return hero;
}

// 一颗星从卡片飞进罐子 (260ms)。尊重 prefers-reduced-motion:
// 减动效时不做飞行, 只留静态文案 (规范 §7.1)。
function flyStarToJar(fromEl) {
  if (!fromEl) return;
  let reduce = false;
  try {
    reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  } catch (e) { reduce = false; }
  if (reduce) return;
  const jar = document.querySelector('.jar');
  if (!jar) return;
  const a = fromEl.getBoundingClientRect();
  const b = jar.getBoundingClientRect();
  const star = el('<span class="kid-fly-star" aria-hidden="true"><svg viewBox="0 0 24 24"><use href="#i-kid-sticker"></use></svg></span>');
  document.body.appendChild(star);
  star.style.left = `${a.left + a.width / 2}px`;
  star.style.top = `${a.top + a.height / 2}px`;
  const dx = (b.left + b.width / 2) - (a.left + a.width / 2);
  const dy = (b.top + b.height / 2) - (a.top + a.height / 2);
  const done = () => { if (star.parentNode) star.parentNode.removeChild(star); };
  if (star.animate) {
    const anim = star.animate(
      [{ transform: 'translate(0, 0) scale(0.6)', opacity: 1 },
       { transform: `translate(${dx}px, ${dy}px) scale(1.1)`, opacity: 0.15 }],
      { duration: 260, easing: 'cubic-bezier(.2,.7,.3,1)' });
    if (anim.onfinish) anim.onfinish = done;
  }
  setTimeout(done, 600);
}

// ====== 庆祝层 (规范 §7.1) ======
//
// 当天必做全部完成时弹全屏庆祝层。减动效时只留静态文案。
let _celebratedFor = null;

// 庆祝记录存在浏览器里 (2026-11-03 用户反馈: "这个框怎么经常弹出")。
//
// 原来只有内存变量 _celebratedFor, 一刷新就丢 —— 于是每次打开页面都会
// 重新弹一遍; 加上翻日期时旧写法只按日期去重, 切一天就等于换一次
// "新的一天", 于是点一次「昨天」又弹一次。
//
// 现在记两处: 内存(本次会话) + localStorage(这个浏览器)。
// 一个日期只弹一次, 换个日期/刷新页面都不再弹。
//
// localStorage 可能不可用 (隐私模式 / 被禁用): 读取失败当作没记录过,
// 写入失败靠内存那一层兜底 —— 宁可多弹一次, 也不能因为存不了就报错。
const CELEBRATE_STORE_KEY = 'st.kid.celebrate';

function readCelebrateRecord() {
  try {
    const raw = window.localStorage.getItem(CELEBRATE_STORE_KEY);
    if (!raw) return null;
    const rec = JSON.parse(raw);
    return (rec && typeof rec === 'object' && typeof rec.date === 'string') ? rec : null;
  } catch (e) {
    return null;
  }
}

function writeCelebrateRecord(date) {
  try {
    window.localStorage.setItem(CELEBRATE_STORE_KEY, JSON.stringify({ date, ts: Date.now() }));
  } catch (e) { /* 存不了就算了, 内存里还有一层去重 */ }
}

// 今天该不该弹庆祝层。四个条件全过才弹:
//
//   1) 看的是**今天** —— 翻「昨天」/历史日期时不能弹「今天全部完成啦!」
//      (2026-11-03 反馈: 弹窗文案写死"今天", 对着昨天的作业喊今天;
//       星星数取的也是今天的 today_earned, 数字同样是错的)
//   2) 今天的必做真的全做完了 —— 跳过的不算数
//   3) 本次会话没弹过
//   4) 这个浏览器今天没弹过 (localStorage)
//
// 纯函数: 时间戳与 localStorage 的读写都留在调用方, 这里只判该不该弹。
// 这样断言不用起浏览器、不用起服务 (tests/test_celebrate_guard.js)。
function shouldCelebrate({ isToday, allDone, date, celebratedDate, savedDate }) {
  if (!isToday) return false;
  if (!allDone) return false;
  if (celebratedDate && celebratedDate === date) return false;
  if (savedDate && savedDate === date) return false;
  return true;
}

function showCelebrate(starsEarned) {
  const reduce = (function () {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) { return false; }
  })();
  const layer = el(`
    <div class="celebrate" role="dialog" aria-label="庆祝">
      <div class="celebrate__card">
        <p class="celebrate__title">今天全部完成啦！</p>
        <p class="celebrate__stars">获得 ${starsEarned} 颗星 ★</p>
        <button type="button" class="celebrate__close">好耶</button>
      </div>
    </div>
  `);
  if (!reduce) {
    // 星星从上方落下
    for (let i = 0; i < 12; i++) {
      const s = el('<span class="celebrate__star" aria-hidden="true"><svg viewBox="0 0 24 24"><use href="#i-kid-sticker"></use></svg></span>');
      s.style.left = `${6 + i * 7.5}%`;
      s.style.animationDelay = `${(i % 6) * 0.12}s`;
      layer.appendChild(s);
    }
  }
  const close = () => { if (layer.parentNode) layer.parentNode.removeChild(layer); };
  layer.querySelector('.celebrate__close').addEventListener('click', close);
  layer.addEventListener('click', (e) => { if (e.target === layer) close(); });
  document.body.appendChild(layer);
}

// ====== 孩子端 · 今日看板 (规范 §4.2) ======

// 点别处时收起所有 ⋯ 菜单。只注册一次 document 监听 —— 每次重绘都往
// document 上挂一个, 看板翻几十次就会攒几十个监听器。
let _kidMenuBound = false;
function bindKidMenus() {
  if (_kidMenuBound) return;
  _kidMenuBound = true;
  document.addEventListener('click', (e) => {
    document.querySelectorAll('.kid-more-menu').forEach(m => {
      if (m.hidden) return;
      const wrap = m.closest('.kid-more-wrap');
      if (wrap && wrap.contains(e.target)) return;   // 点菜单内部不关
      m.hidden = true;
      m.style.display = 'none';
      const btn = wrap ? wrap.querySelector('.kid-more') : null;
      if (btn) btn.setAttribute('aria-expanded', 'false');
      // 关掉菜单要把卡放回原位 —— 抬起来的卡不该继续压着同列的兄弟卡。
      const host = m.closest('.kid-card');
      if (host) host.classList.remove('is-menu-open');
    });
  });
}

// 任务卡。左边一条学科色条, 中间是任务名, 右边只有**一个**大的
// 「完成」按钮 —— 编辑/今天不做/删除 全收进最右边的 ⋯ 菜单。
//
// 布局是**一行**: 任务名占中间, 操作按钮靠右排。
// (初版是三行堆叠 —— 学科行 / 任务名 / 按钮行, 一张卡占掉 140px, 一屏只
// 看得下两三项, 反而像"大卡片"而不像"待办事项"。压成一行后一屏能扫到
// 五六项, 孩子自己就能数出还剩几件事。)
function renderKidCard(item, refresh, isToday = true) {
  bindKidMenus();
  const done = item.status === 'done';
  const skipped = item.status === 'skipped';
  const day = kidDayWord(isToday);
  const skipText = `${day}不做`;
  const card = el(`<div class="kid-card ${done ? 'is-done' : ''} ${skipped ? 'is-skipped' : ''}" data-id="${item.id}">
    <span class="kid-card__bar subject-tag ${kidSubjectClass(item)}" aria-hidden="true"></span>
    <div class="kid-card__main">
      <div class="kid-card__name">${escapeAttr(item.task_name)}</div>
      <div class="kid-card__top">
        <span class="kid-card__subject">${escapeAttr(kidSubjectText(item))}</span>
        <span class="kid-card__est">${item.est_minutes != null ? `约 ${item.est_minutes} 分钟` : ''}</span>
      </div>
    </div>
    <div class="kid-card__acts"></div>
  </div>`);
  const acts = card.querySelector('.kid-card__acts');

  if (done) {
    acts.appendChild(el('<span class="kid-card__done">完成啦 ✓</span>'));
  } else if (skipped) {
    acts.appendChild(el(`<span class="kid-card__done is-skipped">${skipText}</span>`));
  } else {
    const btn = el('<button type="button" class="kid-btn-done">完 成</button>');
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        await api('PATCH', `/assignments/${item.id}/status`, { status: 'done' });
        flyStarToJar(btn);
        await refresh();
      } catch (e) {
        btn.disabled = false;
        toast('没能完成: ' + e.message);
      }
    });
    acts.appendChild(btn);
  }

  // 计时器 (规范 §7.2): 点开始, 再点停止, 停止后把分钟数写进 actual_minutes。
  // 图标形态 (i-clock): 卡片是单行的, 文字按钮会挤掉任务名的宽度。
  acts.appendChild(renderTimerButton(item, { cls: 'kid-timer', icon: 'i-clock', onDone: refresh }));

  // ⋯ 菜单
  const more = el('<span class="kid-more-wrap"></span>');
  const moreBtn = el('<button type="button" class="kid-more" aria-label="更多操作" aria-haspopup="true" aria-expanded="false">'
    + '<svg class="kid-more__ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><use href="#i-kid-more"></use></svg></button>');
  const menu = el('<div class="kid-more-menu" hidden></div>');
  const closeMenu = () => {
    menu.hidden = true;
    menu.style.display = 'none';
    moreBtn.setAttribute('aria-expanded', 'false');
    card.classList.remove('is-menu-open');
  };

  // 菜单往上开还是往下开。
  //
  // 移动端底栏固定在屏幕下沿 (nav#tabs, z-index 900), 往下开的菜单会被底栏
  // 盖住 —— 菜单项一个都点不到 (2026-11-03 用户反馈)。所以量一下空间:
  // 下面不够 (或者干脆比上面还小) 就往上翻, 与自绘下拉的 .dd.is-up 同一套路。
  const placeMenu = () => {
    menu.classList.remove('is-up');
    const btnRect = moreBtn.getBoundingClientRect();
    const menuH = menu.offsetHeight;
    // 底部固定底栏的上沿才是真正能用的"屏幕下沿"; 桌面端 nav 在 header 里,
    // 不是底栏, 那就用视口下沿。
    const nav = document.querySelector('nav#tabs');
    const navRect = nav ? nav.getBoundingClientRect() : null;
    const isBottomBar = !!navRect && navRect.height > 0
      && navRect.bottom >= window.innerHeight - 2;
    const bottomLimit = isBottomBar ? navRect.top : window.innerHeight;
    const spaceBelow = bottomLimit - btnRect.bottom;
    const spaceAbove = btnRect.top;
    if (spaceBelow < menuH + 4 && spaceAbove > spaceBelow) {
      menu.classList.add('is-up');
    }
  };

  moreBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    // 关闭一律走 closeMenu(): 收起状态、aria 与"把卡放回原位"只写一处。
    if (menu.hidden) {
      menu.hidden = false;
      menu.style.display = 'block';
      moreBtn.setAttribute('aria-expanded', 'true');
      placeMenu();
      // 菜单开着时把这张卡抬到同列其它卡之上 (styles.css 的 .kid-card.is-menu-open)。
      // 不抬的话, 完成卡上的 filter 会创建层叠上下文把菜单关在里面, 菜单
      // 就被下一张卡盖住了 (2026-11-03 用户反馈)。
      card.classList.add('is-menu-open');
    } else {
      closeMenu();
    }
  });

  const addItem = (label, fn) => {
    const b = el(`<button type="button" class="kid-more-menu__item">${label}</button>`);
    b.addEventListener('click', async (e) => {
      e.stopPropagation();
      closeMenu();
      await fn();
    });
    menu.appendChild(b);
  };
  if (!done && !skipped) {
    addItem(skipText, async () => {
      try {
        await api('PATCH', `/assignments/${item.id}/status`, { status: 'skipped' });
        toast(`好, ${day}先不做`);
        await refresh();
      } catch (e) { toast('操作失败: ' + e.message); }
    });
  }
  if (done) {
    addItem('再做到一次', async () => {
      try {
        await api('PATCH', `/assignments/${item.id}/status`, { status: 'todo' });
        await refresh();
      } catch (e) { toast('操作失败: ' + e.message); }
    });
  }
  addItem('编辑', async () => { openEditAssignmentModal(item, refresh); });
  if (!done) {
    addItem('删除', async () => {
      const ok = await confirmDialog({
        title: '删掉这条作业?',
        lead: '这条作业会消失, 而且找不回来。',
        target: item.task_name,
        subject: item.subject || '',
        okText: '删除',
        danger: true,
      });
      if (!ok) return;
      try {
        await api('DELETE', `/assignments/${item.id}`);
        toast('已删除');
        await refresh();
      } catch (e) { toast('删除失败: ' + e.message); }
    });
  }
  more.append(moreBtn, menu);
  // ⋯ 放最右 (而不是塞进学科行): 单行布局里它得贴着卡片右边缘,
  // 位置固定, 孩子扫一眼就知道"更多的操作都在这儿"。
  card.appendChild(more);
  return card;
}

// 孩子端今日看板。
async function renderKidBoard(b, root) {
  root.innerHTML = '';
  const titleEl = document.getElementById('board-title');
  if (titleEl) titleEl.textContent = b.label === '今天' ? '今日' : (b.label || '今日');

  // 看板能翻历史日期 —— "今天"这两个字只在看今天时才能用, 文案与星星
  // 都要跟着日期走 (2026-11-03 用户反馈: 点「昨天」, 满屏还是"今天")。
  //
  // 后端给了 is_today 就用它; 万一这个字段没了, 用本地日期兜底 ——
  // 判断错的方向是"该说这天却说今天", 孩子会以为根本没翻页。
  const isToday = (b.is_today !== undefined) ? !!b.is_today : (b.date === todayStr());

  // 星星按**看板看的那一天**取: 翻到昨天, 罐子画的是昨天拿到的星星,
  // 连续天数也截止昨天 (后端 /kid/stars?date= 就是这么算的)。
  const stars = await loadKidStars(b.date);

  // 必做: 学科 + 培训 + 习惯都算今天的活。选做单算。
  const all = [];
  for (const subj of SUBJECT_ORDER) all.push(...((b.by_subject || {})[subj] || []));
  for (const kind of ['training', 'habit']) all.push(...((b.by_kind || {})[kind] || []));
  const pending = all.filter(i => !isSettledItem(i));
  const settled = all.filter(i => isSettledItem(i));
  const optional = b.optional_items || [];

  root.appendChild(renderKidHero({
    remaining: pending.length,
    stars: stars,
    doneCount: settled.filter(i => i.status === 'done').length,
    isToday: isToday,
  }));

  if (!all.length && !optional.length) {
    root.appendChild(el(`<div class="kid-empty">
      <p class="kid-empty__text">${isToday ? '今天没有作业，好好休息一下' : '这天没有作业'}</p>
    </div>`));
    return;
  }

  const refresh = () => loadBoard();

  if (all.length) {
    root.appendChild(el('<h3 class="kid-section">必须完成</h3>'));
    const list = el('<div class="kid-list"></div>');
    pending.forEach(item => list.appendChild(renderKidCard(item, refresh, isToday)));
    settled.forEach(item => list.appendChild(renderKidCard(item, refresh, isToday)));
    root.appendChild(list);
  }

  if (optional.length) {
    root.appendChild(el('<h3 class="kid-section">有空再做</h3>'));
    const list = el('<div class="kid-list"></div>');
    optional.forEach(item => list.appendChild(renderKidCard(item, refresh, isToday)));
    root.appendChild(list);
  }

  // 当天必做全部完成 -> 庆祝层。
  //
  // 三个约束 (2026-11-03 用户反馈):
  //   - 只在"今天"弹: 翻「昨天」看历史不该收到"今天全部完成啦!"
  //   - 真的全做完了才弹 (跳过的不算)
  //   - 一个日期只弹一次 (浏览器记住, 刷新/切日期都不再弹)
  const doneAll = all.length > 0 && pending.length === 0
    && all.every(i => i.status === 'done');
  const rec = readCelebrateRecord();
  if (shouldCelebrate({
    isToday: isToday,
    allDone: doneAll,
    date: b.date,
    celebratedDate: _celebratedFor,
    savedDate: rec ? rec.date : null,
  })) {
    _celebratedFor = b.date;
    writeCelebrateRecord(b.date);
    showCelebrate(stars ? (stars.today_earned || 3) : 3);
  }
}

// ====== 孩子端 · 贴纸册日历 (规范 §4.3) ======

// 当天贴纸状态: 全完成 / 部分完成 / 留白。
function kidStickerState(day) {
  if (!day || !day.total) return 'none';
  if (day.required > 0 && day.required_done === day.required) return 'full';
  if (day.settled > 0 || day.required_done > 0) return 'half';
  return 'todo';
}

// 按天取一次当天作业 (只为挑贴纸的学科色)。缓存住, 翻月份时不会重复拉。
const _kidDayCache = new Map();
function kidDayItems(ds) {
  if (!_kidDayCache.has(ds)) {
    _kidDayCache.set(ds, api('GET', `/assignments?date=${ds}`)
      .then(r => ({ items: (r && r.items) || [] }))
      .catch(() => ({ items: [] })));
  }
  return _kidDayCache.get(ds);
}

// 当天最主要的学科 (按条数投票)。取不到就返回 null, 贴纸用默认色。
function dominantSubject(items) {
  const counts = {};
  items.forEach(i => {
    if (!i.subject) return;
    counts[i.subject] = (counts[i.subject] || 0) + 1;
  });
  const keys = Object.keys(counts);
  if (!keys.length) return null;
  keys.sort((a, b) => counts[b] - counts[a]);
  return keys[0];
}

// 贴纸册。格子还是原来的 .cal-cell (移动端布局与既有 CSS 继续适用),
// 里面的贴纸是 .kid-sticker。
function renderKidCalendar(grid, r) {
  grid.innerHTML = '';
  for (let i = 0; i < r.first_weekday; i++) {
    grid.appendChild(el('<div class="cal-cell blank"></div>'));
  }
  for (let d = 1; d <= r.days_in_month; d++) {
    const ds = dateStr(r.year, r.month, d);
    const day = (r.days || {})[ds];
    const weekday = (r.first_weekday + d - 1) % 7;
    const state = kidStickerState(day);
    const sticker = state === 'none' || state === 'todo'
      ? ''
      : `<svg class="kid-sticker kid-sticker--${state}" aria-hidden="true" focusable="false" viewBox="0 0 24 24"><use href="#i-kid-sticker"></use></svg>`;
    const label = state === 'full' ? '全部完成'
      : state === 'half' ? '完成了一部分'
      : day && day.total ? '还没开始' : '没有作业';
    const cell = el(`<button class="cal-cell kid-cell kid-cell--${state} ${ds === _calSelected ? 'selected' : ''} ${weekday >= 5 ? 'we' : ''} ${ds === todayStr() ? 'is-today kid-cell--today' : ''}"
        data-date="${ds}" aria-label="${ds} ${label}">
      <span class="cal-day">${d}</span>
      ${sticker}
    </button>`);
    cell.addEventListener('click', () => selectDate(ds));
    grid.appendChild(cell);
  }
  // 贴纸的学科色要按天取, 拿到之后再补上 (先画出来, 别让颜色把整页卡住)。
  Object.keys(r.days || {}).forEach(ds => {
    if (!r.days[ds] || !r.days[ds].total) return;
    kidDayItems(ds).then(info => {
      const subj = dominantSubject(info.items || []);
      if (!subj) return;
      const color = subjectColor(subj);
      if (!color) return;
      grid.querySelectorAll(`.kid-cell[data-date="${ds}"] .kid-sticker`).forEach(s => {
        s.style.color = color;
      });
    }).catch(() => {});
  });
}

// ====== 孩子端 · 日历当日详情 ======
function renderKidDayDetail(root, d, items) {
  root.innerHTML = '';
  if (!items.length) {
    root.appendChild(el('<div class="kid-empty"><p class="kid-empty__text">这天没有作业</p></div>'));
    return;
  }
  const refresh = () => loadList(d);
  const required = items.filter(i => !i.is_optional);
  const optional = items.filter(i => i.is_optional);
  if (required.length) {
    root.appendChild(el('<h3 class="kid-section">必须完成</h3>'));
    const list = el('<div class="kid-list"></div>');
    required.forEach(i => list.appendChild(renderKidCard(i, refresh)));
    root.appendChild(list);
  }
  if (optional.length) {
    root.appendChild(el('<h3 class="kid-section">有空再做</h3>'));
    const list = el('<div class="kid-list"></div>');
    optional.forEach(i => list.appendChild(renderKidCard(i, refresh)));
    root.appendChild(list);
  }
}

// 勋章图标: 后端给的是**内联 SVG 的名字**(不是 emoji), 这里映射到
// 本页 symbol; 没有对应的就用通用勋章图形。
function medalSymbol(icon) {
  const map = {
    star: 'i-kid-sticker',
    flame: 'i-kid-medal',
    shield: 'i-kid-medal',
    trophy: 'i-kid-medal',
    medal: 'i-kid-medal',
  };
  return map[icon] || 'i-kid-medal';
}

// ====== 孩子端 · 我的成绩单 (规范 §4.4) ======

// 评语只描述数据, 不夸不贬 ("这周完成了 12 项作业, 比上周多 2 项")。
function kidReportComment(report) {
  const c = report && report.completion ? report.completion : {};
  const tn = report && report.trend ? report.trend : {};
  const done = c.done || 0;
  const delta = tn.done_delta || 0;
  let s = `这周完成了 ${done} 项作业`;
  if (delta > 0) s += `，比上周多 ${delta} 项`;
  else if (delta < 0) s += `，比上周少 ${Math.abs(delta)} 项`;
  return s + '。';
}

async function renderKidReport(root, period) {
  root.innerHTML = '';
  const wrap = el('<div class="kid-report"></div>');
  const [stars, medals, report] = await Promise.all([
    loadKidStars(),
    api('GET', '/kid/medals').catch(() => null),
    api('GET', '/weekly-report').catch(() => null),
  ]);

  const rate = report && report.completion ? (report.completion.rate || 0) : 0;
  wrap.appendChild(el(`
    <div class="kid-report__banner">
      <div class="kid-report__rate">${rate}<span class="kid-report__unit">%</span></div>
      <div class="kid-report__text">
        <p class="kid-report__comment">${escapeAttr(kidReportComment(report))}</p>
        <p class="kid-report__sub">本周完成率</p>
      </div>
    </div>
  `));

  // 勋章墙: 得到的实心, 没得到的浅色轮廓 + 锁。
  const wall = el('<div class="medal-wall"></div>');
  const earned = medals && medals.items ? medals.items : [];
  const locked = medals && medals.locked ? medals.locked : [];
  const all = earned.concat(locked);
  if (all.length) {
    all.forEach(m => {
      const got = earned.indexOf(m) >= 0;
      const name = m.name || m.medal_key || m.key || '';
      const medal = el(`<div class="medal ${got ? '' : 'medal--locked'}">
        <span class="medal__ico" aria-hidden="true"><svg viewBox="0 0 24 24"><use href="#${medalSymbol(m.icon)}"></use></svg></span>
        <span class="medal__name">${escapeAttr(name)}</span>
        <span class="medal__desc">${escapeAttr(got ? (m.desc || '') : '还没拿到')}</span>
      </div>`);
      wall.appendChild(medal);
    });
  } else {
    wall.appendChild(el('<p class="muted">勋章还没解锁, 完成作业就会点亮第一枚</p>'));
  }
  wrap.appendChild(el('<h3 class="kid-section">我的勋章</h3>'));
  wrap.appendChild(wall);

  // 本周星星明细
  wrap.appendChild(el('<h3 class="kid-section">这周的星星</h3>'));
  const week = stars && stars.week ? stars.week : null;
  const ledger = (stars && stars.ledger) || [];
  if (week) {
    wrap.appendChild(el(`<div class="kid-report__stars">
      <span class="kid-report__star-num">${week.stars || 0}</span>
      <span class="muted">颗 · 活跃 ${week.days_active || 0} 天 · 完成 ${week.tasks_done || 0} 项</span>
    </div>`));
  }
  if (ledger.length) {
    const ul = el('<ul class="kid-star-list"></ul>');
    ledger.slice(0, 10).forEach(x => {
      // 家长手动奖励时 note 里有他写的那句话 (迁移 v6)。有话就显示话,
      // 孩子最想看的是"爸爸为什么奖励我", 不是"奖励"两个字。
      const why = x.note ? String(x.note) : kidStarReason(x.reason);
      ul.appendChild(el(`<li><span class="kid-star-list__date">${escapeAttr(String(x.date || '').slice(5))}</span>`
        + `<span class="kid-star-list__reason">${escapeAttr(why)}</span>`
        + `<span class="kid-star-list__n">${x.stars > 0 ? '+' : ''}${x.stars}</span></li>`));
    });
    wrap.appendChild(ul);
  } else {
    wrap.appendChild(el('<p class="muted">还没有星星记录, 做完一项就有啦</p>'));
  }

  root.appendChild(wrap);
}

// 星星用途的 kid 口径说法 (后端 reason 是 code)。
function kidStarReason(reason) {
  return {
    earn_task: '完成一项作业',
    revoke_task: '撤销了一次完成',
    bonus_all_done: '当天全部完成',
    bonus_manual: '奖励',
    spend: '花掉星星',
  }[reason] || '星星变动';
}

// ====== 家长端 · 复盘增强 (规范 §4.6) ======
//
// 2026-11-02 重写。这里的三张图 (环形图 / 折线图 / 柱状图) 在此之前
// 散在这两个文件里, 而且**内容重复**:
//
//   app.js 的「各科时间占比」(预计/实际两张饼)
//     ≡ dual.js 的「学科用时分配」(一张环, 实际占比)
//   app.js 的「每日作业用时」(堆叠柱状)
//     ≡ dual.js 的「每日用时趋势」(折线, 最近 7 天)
//
// 四张图只有两份独立数据, 家长滚两屏看到的是同一件事的四种画法。
// 现在收敛成**两张图**: 每日用时 (ECharts 堆叠柱) + 学科占比 (ECharts 环,
// 可切预估/实际)。画法交给 charts.js, 这里只负责组装与叙述。

// 晚间小结的标签 -> 中文。跟后端 daily_wrap_service 的标签口径一致。
const DW_TAG_TEXT = {
  slow_writing: '写字慢', reread: '读题走神', many_errors: '错题多',
  unfamiliar: '内容不熟', good_pace: '速度正好', focused: '很专心',
};

// 薄弱项: 从当前周期的估时偏差 + 晚间小结的标签里推出结论。
//
// **必须跟着当前周期走** (2026-11-02 修的缺陷):
//   原来这里读的是 /weekly-report, 而那个接口永远算的是**本周** ——
//   选「上周」时, 薄弱项分析的是本周的作业。实测: 上周有 705 分钟数据,
//   薄弱项却显示"这一段还没有足够的记录"。现在改读当前周期的
//   /api/analytics/review 返回值 (deviation), 周期对了结论才对。
function renderPWeak(deviation, tagCounts) {
  const lines = [];
  const under = (deviation && deviation.top_underestimate) || [];
  const over = (deviation && deviation.top_overestimate) || [];
  if (under.length) {
    const top = under[0];
    lines.push(`这个区间${top.subject}${top.task_type || '作业'}平均多花 ${top.diff_minutes} 分钟, 是最该盯的一项。`);
  } else if (over.length) {
    const top = over[0];
    lines.push(`这个区间${top.subject}${top.task_type || '作业'}平均少花 ${Math.abs(top.diff_minutes)} 分钟, 估得偏保守。`);
  }
  const tagKeys = Object.keys(tagCounts || {}).sort((a, b) => tagCounts[b] - tagCounts[a]);
  if (tagKeys.length) {
    const t = tagKeys[0];
    lines.push(`晚间小结里出现最多的是「${DW_TAG_TEXT[t] || t}」(${tagCounts[t]} 次)。`);
  }
  if (!lines.length) {
    // 样本够但估时与实耗完全吻合, 和"记录还不够"是两回事:
    // 后者劝家长"多记几天", 前者根本没那个问题。混成一句话会让家长
    // 以为还欠着记录, 其实该夸的是估得准 (2026-11-02 实测: 上周 41 条
    // 记录偏差全为 0, 却被说成"还没有足够的记录")。
    const sample = (deviation && deviation.sample_size) || 0;
    if (sample >= 5) {
      lines.push(`这段时间估的时长和实际用时基本吻合 (${sample} 条记录), 没看出明显偏差。`);
    } else {
      lines.push('这一段还没有足够的记录, 多记几天就能看出薄弱项。');
    }
  }
  return el(`<div class="p-weak">
    <div class="p-weak__title">薄弱项</div>
    ${lines.map(t => `<div class="p-weak__line">${escapeAttr(t)}</div>`).join('')}
  </div>`);
}

// 晚间小结里出现过的标签, 统计各出现几次。
// 最多 7 次请求, 只在复盘页发生。
async function collectReflectionTags(dates) {
  const counts = {};
  const results = await Promise.all(dates.map(d =>
    api('GET', `/daily-wrap?date=${d}`).catch(() => null)));
  results.forEach(w => {
    const tags = w && w.reflection ? w.reflection.tags : null;
    const list = Array.isArray(tags) ? tags
      : String(tags || '').split(',').map(s => s.trim()).filter(Boolean);
    list.forEach(t => { counts[t] = (counts[t] || 0) + 1; });
  });
  return counts;
}

// 「挪到下周」: 把 due_date 改到下周一 (走既有的 PATCH /api/assignments/{id})。
function nextMondayIso() {
  const d = new Date();
  const dow = d.getDay();               // 0=周日
  const delta = (8 - dow) % 7 || 7;     // 到下一个周一
  d.setDate(d.getDate() + delta);
  const p = x => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// 给周报里"还没收尾"的每一条挂上「挪到下周」。
// 周报返回的未完成项里**没有 assignment id** (后端没给), 所以按日期
// 取一次当天作业, 用任务名 + 学科对上号 —— 对不上的就不给按钮。
async function attachDeferButtons() {
  const lis = Array.from(document.querySelectorAll('#review-content .report-list li[data-date]'));
  if (!lis.length) return;
  const byDate = {};
  lis.forEach(li => {
    const d = li.dataset.date;
    if (!d) return;
    (byDate[d] = byDate[d] || []).push(li);
  });
  const monday = nextMondayIso();
  for (const d of Object.keys(byDate)) {
    let items = [];
    try {
      const r = await api('GET', `/assignments?date=${d}`);
      items = (r && r.items) || [];
    } catch (e) { items = []; }
    byDate[d].forEach(li => {
      const hit = items.find(i =>
        i.task_name === li.dataset.task
        && (!li.dataset.subject || !i.subject || i.subject === li.dataset.subject));
      if (!hit) return;
      const btn = el('<button type="button" class="p-action">挪到下周</button>');
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await api('PATCH', `/assignments/${hit.id}`, { due_date: monday });
          toast('已挪到下周一');
          li.classList.add('is-deferred');
          btn.disabled = false;
        } catch (e) {
          btn.disabled = false;
          toast('挪动失败: ' + e.message);
        }
      });
      li.appendChild(btn);
    });
  }
}

// 家长端复盘的**薄弱项卡**。
//
// 数据**由调用方传进来** (app.js 的 loadReview 已经拉过了):
// 之前这里自己又打一遍 /analytics/review-time, 同一个接口每次切周期
// 被请求两次 —— 一次请求回来的数据, 拿两次没有意义。
//
// 只返回薄弱项这一块: 两张图与月报由 app.js 自己拼 (复盘页的结构归
// app.js 管, 这里只出"角色专属"的那一块)。
async function renderParentReviewExtras(period, ctx) {
  ctx = ctx || {};
  // 薄弱项要读晚间小结的标签: 最多 7 次请求, 只在复盘页发生。
  // 日期范围跟着当前周期走 —— 上周周期就该看上周的小结;
  // 上月周期看的是**那个月**的小结 (ctx.month), 不是"最近 31 天"。
  const dates = reflectionTagDates(period, ctx.month);
  const tagCounts = await collectReflectionTags(dates);
  const card = renderPWeak(ctx.deviation, tagCounts);
  attachDeferButtons();
  return card;
}

// 薄弱项要读哪些天的晚间小结。跟着当前周期走 ——
// 上周周期就该看上周的小结, 而不是永远看最近七天。
//
// month: 月视图周期 ('last_month') 下看的是哪个月 ('YYYY-MM')。
// 月视图要读那个月的**实际**日期 —— 翻回 8 月看 8 月的作业, 配的却是
// "最近 31 天"的小结, 薄弱项就张冠李戴了。
function reflectionTagDates(period, month) {
  const dates = [];
  if (period === 'last') {
    for (let i = 7; i >= 1; i--) dates.push(addDays(todayStr(), -i));
  } else if (period === 'last_month' && month) {
    // 该月的 1 号 ~ 最后一天。new Date(y, m, 0) 是"下个月的 0 号",
    // 也就是这个月的最后一天 (2 月、平年闰年都自动对)。
    const [y, m] = month.split('-').map(Number);
    const lastDay = new Date(y, m, 0).getDate();
    const mm = String(m).padStart(2, '0');
    for (let d = 1; d <= lastDay; d++) {
      dates.push(`${y}-${mm}-${String(d).padStart(2, '0')}`);
    }
  } else if (period === 'month') {
    for (let i = 30; i >= 0; i--) dates.push(addDays(todayStr(), -i));
  } else {
    for (let i = 6; i >= 0; i--) dates.push(addDays(todayStr(), -i));
  }
  return dates;
}

// ====== 家长端 · 日历悬停详情卡 ======
//
// 数据能拿到的: /assignments/calendar 给完成率与总用时;
// 各科用时与课外班安排要从另外两个接口补 (按天 / 按周), 拿不到就只显示
// 前两项 —— 不能为了凑齐三行去改后端。
let _calTip = null;

function initCalHover() {
  const grid = document.getElementById('cal-grid');
  if (!grid || grid._hoverBound) return;
  grid._hoverBound = true;
  grid.addEventListener('mouseover', (e) => {
    const cell = e.target.closest && e.target.closest('.cal-cell');
    if (!cell || !cell.dataset.date) return;
    // 悬停详情卡是家长端的工具 (完成率 / 各科用时 / 课外班)。孩子端的日历是
    // 贴纸册, 悬停出这张卡只会把孩子搞懵 —— 那边靠点格子看详情。
    if (typeof isKidRole === 'function' && isKidRole()) return;
    showCalTip(cell);
  });
  grid.addEventListener('mouseout', (e) => {
    const cell = e.target.closest && e.target.closest('.cal-cell');
    if (!cell) return;
    if (e.relatedTarget && cell.contains(e.relatedTarget)) return;
    hideCalTip();
  });
}

function calTipEl() {
  if (!_calTip) {
    _calTip = el('<div class="cal-tip" role="tooltip" hidden></div>');
    document.body.appendChild(_calTip);
  }
  return _calTip;
}

function hideCalTip() {
  if (_calTip) { _calTip.hidden = true; _calTip.style.display = 'none'; }
}

// 悬停卡定位: 优先落在格子下方, 下方放不下就翻到上方, 再夹进视口内。
// 卡是 fixed 定位, 坐标就是视口坐标 —— 页面滚动时不用补 scrollY。
//
// 为什么要重定位两次: 各科用时 / 课外班是异步回来的, 第一次画出来时卡里
// 写的是"读取中…", 高度只有一行; 数据回来后换成两三行, 卡变高, 位置必须
// 再算一次, 否则下方放不下的那次会把卡的底边顶出视口。
function positionCalTip(cell) {
  const tip = calTipEl();
  if (!tip || tip.hidden) return;
  const r = cell.getBoundingClientRect();
  const w = tip.offsetWidth || 240;
  const h = tip.offsetHeight || 90;
  const gap = 8;
  let top = r.bottom + gap;
  if (top + h > window.innerHeight - gap) {
    top = r.top - h - gap;
  }
  top = Math.max(gap, Math.min(top, window.innerHeight - h - gap));
  const left = Math.max(gap, Math.min(r.left, window.innerWidth - w - gap));
  tip.style.top = `${Math.round(top)}px`;
  tip.style.left = `${Math.round(left)}px`;
}

function showCalTip(cell) {
  const ds = cell.dataset.date;
  const day = (_calData && _calData.days) ? _calData.days[ds] : null;
  const tip = calTipEl();
  const rate = day && day.required
    ? Math.round((day.required_done || 0) / day.required * 100)
    : (day ? 100 : 0);
  const special = (_calData && _calData.special) ? _calData.special[ds] : null;
  tip.innerHTML = `
    <div class="cal-tip__date">${escapeAttr(ds)}${special ? ' · ' + escapeAttr(special.name) : ''}</div>
    <div class="cal-tip__row"><span>完成率</span><b>${day && day.total ? rate + '%' : '—'}</b></div>
    <div class="cal-tip__row"><span>总用时</span><b>${day ? (day.actual_total || 0) + ' 分' : '—'}</b></div>
    <div class="cal-tip__row cal-tip__row--subj"><span>各科用时</span><b>读取中…</b></div>
    <div class="cal-tip__row cal-tip__row--plan"><span>课外班</span><b>—</b></div>`;
  tip.hidden = false;
  tip.style.display = 'block';
  positionCalTip(cell);

  // 各科用时: 按天取一次 (缓存)
  kidDayItems(ds).then(info => {
    if (!tip.isConnected) return;
    const items = (info.items || []).filter(i => i.status === 'done');
    const bySubj = {};
    items.forEach(i => {
      if (!i.subject) return;
      const mins = (i.actual_minutes != null ? i.actual_minutes : i.est_minutes) || 0;
      bySubj[i.subject] = (bySubj[i.subject] || 0) + mins;
    });
    const keys = Object.keys(bySubj);
    const box = tip.querySelector('.cal-tip__row--subj b');
    if (!box) return;
    // 学科名带学科色 —— 全站统一色板, 家长扫一眼就知道是哪科占了多少时间。
    box.innerHTML = keys.length
      ? keys.map(k => `<span class="cal-tip__subj"><i style="background:${subjectColor(k)}"></i>${escapeAttr(k)} ${bySubj[k]}分</span>`).join('')
      : '—';
    positionCalTip(cell);
  }).catch(() => {});

  // 课外班: 周计划里当天有没有课
  const anchor = addDays(ds, -((new Date(ds + 'T00:00:00').getDay() + 6) % 7));
  api('GET', `/week-plan?date=${anchor}`).then(w => {
    if (!tip.isConnected) return;
    const box = tip.querySelector('.cal-tip__row--plan b');
    if (!box) return;
    const d = (w && w.days) ? w.days.find(x => x.date === ds) : null;
    const slots = d && d.slots ? d.slots : [];
    // 周计划里的课外班字段是 project_name / start_time / end_time ——
    // 早先这里读的是 s.name, 取不到值, 一律落到 '课' 这个占位词上。
    box.textContent = slots.length
      ? slots.map(s => {
        const name = s.project_name || s.name || s.subject || '课外班';
        const t = s.start_time && s.end_time ? ` ${s.start_time}-${s.end_time}` : '';
        return name + t;
      }).join(' · ')
      : '没有安排';
    positionCalTip(cell);
  }).catch(() => {});
}

// ====== 家长端 PIN 门槛 (规范 §1.2) ======
//
// 前端**不存明文 PIN**: 输入的 4 位只发给后端 (PBKDF2 存哈希),
// 连 localStorage 都不落。
// 区分"密码错了"与"服务没上线": 家长验证服务还没部署时, 接口会 404,
// 那不是密码错, 不能拿它去累计失败次数 (连按三次就被锁 60 秒, 更恼人)。
function isServiceError(e) {
  const m = String((e && e.message) || '');
  return m.includes('404') || m.includes('???') || m.includes('Not Found')
    || m.includes('Failed to fetch') || m.includes('NetworkError');
}

function openPinGate(onPass) {
  // 同一时间只留一个门槛: 上一个没关干净 (比如刚输对密码时) 再开一个,
  // 页面上会叠着两个弹层, 用户会不知道该点哪个。
  document.querySelectorAll('.pin-gate').forEach(g => {
    if (g.parentNode) g.parentNode.removeChild(g);
  });
  const gate = el(`<div class="pin-gate" role="dialog" aria-modal="true" aria-label="家长验证">
    <div class="pin-gate__box">
      <h3 class="pin-gate__title">家长验证</h3>
      <p class="pin-gate__hint">正在检查…</p>
      <div class="pin-dots" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
      <input class="pin-input" type="tel" inputmode="numeric" maxlength="4"
             autocomplete="off" aria-label="4 位密码" />
      <p class="pin-gate__msg" role="alert"></p>
      <div class="pin-keypad" role="group" aria-label="数字键盘">
        ${[1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => `<button type="button" class="pin-key" data-d="${n}">${n}</button>`).join('')}
        <button type="button" class="pin-key pin-key--fn" data-d="back">删除</button>
        <button type="button" class="pin-key" data-d="0">0</button>
        <button type="button" class="pin-key pin-key--fn" data-d="clear">清空</button>
      </div>
      <div class="pin-gate__foot">
        <button type="button" class="pin-gate__cancel">取消</button>
        <button type="button" class="pin-gate__skip" hidden>直接进入</button>
      </div>
    </div>
  </div>`);
  document.body.appendChild(gate);
  const hint = gate.querySelector('.pin-gate__hint');
  const msg = gate.querySelector('.pin-gate__msg');
  const dots = Array.from(gate.querySelectorAll('.pin-dots i'));
  const input = gate.querySelector('.pin-input');
  const skipBtn = gate.querySelector('.pin-gate__skip');

  let mode = 'verify';          // 'verify' | 'setup'
  let pin = '';
  let fails = 0;
  let lockedUntil = 0;

  const close = () => { if (gate.parentNode) gate.parentNode.removeChild(gate); };

  const paint = () => {
    dots.forEach((d, i) => d.classList.toggle('is-on', i < pin.length));
    input.value = pin;
  };

  const lockFor = (sec) => {
    lockedUntil = Date.now() + sec * 1000;
    gate.querySelectorAll('.pin-key').forEach(b => { b.disabled = true; });
    const tick = () => {
      const left = Math.ceil((lockedUntil - Date.now()) / 1000);
      if (left <= 0) {
        gate.querySelectorAll('.pin-key').forEach(b => { b.disabled = false; });
        msg.textContent = '';
        return;
      }
      msg.textContent = `密码错了 ${fails} 次, 请 ${left} 秒后再试`;
      setTimeout(tick, 500);
    };
    tick();
  };

  const submit = async () => {
    if (pin.length !== 4) { msg.textContent = '要 4 位数字'; return; }
    if (mode === 'setup') {
      try {
        await api('POST', '/kid/pin', { pin });
        close();
        toast('密码设好了');
        if (onPass) onPass();
      } catch (e) {
        if (isServiceError(e)) {
          msg.textContent = '验证服务暂时不可用, 可以先直接进入。';
          skipBtn.hidden = false;
          return;
        }
        msg.textContent = '设置失败: ' + e.message;
        pin = '';
        paint();
      }
      return;
    }
    try {
      await api('POST', '/kid/pin/verify', { pin });
      close();
      if (onPass) onPass();
    } catch (e) {
      if (isServiceError(e)) {
        msg.textContent = '验证服务暂时不可用, 可以先直接进入。';
        skipBtn.hidden = false;
        return;
      }
      fails += 1;
      pin = '';
      paint();
      if (fails >= 3) {
        lockFor(60);
      } else {
        msg.textContent = `密码错了 ${fails} 次`;
      }
    }
  };

  gate.querySelectorAll('.pin-key').forEach(b => {
    b.addEventListener('click', () => {
      const d = b.dataset.d;
      if (d === 'back') pin = pin.slice(0, -1);
      else if (d === 'clear') pin = '';
      else if (pin.length < 4) pin += d;
      msg.textContent = '';
      paint();
      if (pin.length === 4) submit();
    });
  });
  input.addEventListener('input', () => {
    pin = input.value.replace(/\D/g, '').slice(0, 4);
    paint();
    if (pin.length === 4) submit();
  });
  gate.querySelector('.pin-gate__cancel').addEventListener('click', close);
  skipBtn.addEventListener('click', () => { close(); if (onPass) onPass(); });

  // 先问后端: 家长端到底设没设密码。
  api('GET', '/kid/pin/status')
    .then(st => {
      mode = st && st.enabled ? 'verify' : 'setup';
      hint.textContent = mode === 'setup'
        ? '首次使用，请先设置 4 位密码'
        : '请输入 4 位密码';
    })
    .catch(() => {
      // 家长验证服务还没上线: 不该把人堵在门外。给一个"直接进入"的出口,
      // 同时把原因说清楚。
      mode = 'verify';
      hint.textContent = '家长验证服务暂时不可用';
      msg.textContent = '验证服务未就绪, 可以先直接进入。';
      skipBtn.hidden = false;
    });

  try { input.focus(); } catch (e) { /* 某些浏览器 focus 会抛, 无所谓 */ }
  return gate;
}

// ====== 启动接线 ======
// app.js 先跑完, 这里再补上"只在孩子端/家长端才需要"的那部分:
// 日历悬停详情卡、角色切换后的日历模式同步。
document.addEventListener('DOMContentLoaded', initCalHover);
if (document.readyState !== 'loading') initCalHover();

// 角色切换后, 日历的模式按钮与默认模式要跟着换。
window.addEventListener('hashchange', () => {
  if (typeof syncCalendarMode === 'function') syncCalendarMode();
  hideCalTip();
});

// 悬停卡是 fixed 定位, 一旦页面滚动就会跟格子脱节 (还停在旧坐标), 所以
// 滚动时直接收掉。capture=true 是为了连内层滚动容器一起盖住。
window.addEventListener('scroll', hideCalTip, true);

// 告诉 app.js: 双角色渲染器已就位。
// app.js 用它兜底 —— 万一将来脚本顺序被改回 (dual.js 排在 app.js 之后),
// 它会等这个事件再重画当前页, 而不是静默走错分支渲染出家长端的内容。
document.dispatchEvent(new CustomEvent('dual-ready'));
