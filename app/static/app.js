// ====== study-tracker 前端 (视觉对齐版 · 2026-09-28) ======
// 配套 docs/02-页面设计.md §9 "给码灵实现的 4 个前端坑"
// 1. 数字 tabular-nums (在 styles.css 全局声明)
// 2. pending_actual 中间态: done 后未填实耗, 输入框高亮 + Toast 兜底
// 3. 低置信度任务显眼: 整行浅红底 + 暗红边条 + ⚠ 徽章
// 4. "手动添加" 按钮始终可见, AI 不可用时加暗红描边
'use strict';

const API = '/api';
const MAX_TEXT_LEN = 10000;

// 已挂载的项目选择器 (自绘下拉)。refreshProjectPickers() 靠它把"项目列表
// 变了"这件事同步给所有还开着的选择器 —— 声明放在文件顶部, 保证模块里
// 任何一处 init 调用它时这个数组已经建好。
const _projectPickers = [];

// ====== 工具 ======
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(API + path, opts);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
  if (!r.ok) {
    const err = (data && data.error) || { code: '???', message: text || r.statusText };
    throw new Error(`[${err.code}] ${err.message}`);
  }
  return data;
}

function todayStr() {
  // 必须用本地日期。toISOString() 取的是 UTC, 在 UTC+8 的 00:00-08:00
  // 期间会退到前一天, 导致"今天"的作业被记到昨天, 早上打开看板一片空白。
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function fmtDate(s) {
  if (!s) return '—';
  return s.slice(0, 10);
}

function fmtDateTime(s) {
  if (!s) return '—';
  return s.replace('T', ' ').slice(0, 16);
}

// 置信度三档的元数据 (2026-09-28)
// 先定义再用: confidenceDot() 内部要读它, 顺序颠倒虽然因函数体
// 延迟求值而不会真的抛错, 但读起来容易误判为有坑。
const CONFIDENCE_META = {
  high:   { short: '高', icon: '●', tip: 'AI 高度确信' },
  medium: { short: '中', icon: '◐', tip: 'AI 推断, 建议核对原文' },
  low:    { short: '低', icon: '⚠', tip: 'AI 推断不完整, 建议点开原文核对' },
};

// 置信度标记 (设计文档 §5.4 + §9 坑 3)
// 表格里的紧凑版: 只出图标, 文字移到 title 与 aria-label。
// 原来用「⚠ 需核对」这样的多字标签, 在预览表窄列里必然折行
// (「需核对」竖着排三行), 把整行撑高一大截 —— 图上 3 行预览
// 高得离谱就是这个原因。图标 + 悬停提示信息量不减, 宽度省一大截。
function confidenceDot(level) {
  const conf = CONFIDENCE_META[level] || CONFIDENCE_META.medium;
  return `<span class="conf-dot ${level}" title="${conf.tip}" aria-label="置信度${conf.short}">${conf.icon}</span>`;
}

// 带文字的置信度标签。给宽度充裕的地方用(看板行、原文弹窗)——
// 那里的列不会被挤窄, 多几个字能省掉一次悬停。
function confidencePill(level) {
  const conf = CONFIDENCE_META[level] || CONFIDENCE_META.medium;
  const label = { high: '高', medium: '推断', low: '需核对' }[level] || level;
  return `<span class="confidence-pill ${level}" title="${conf.tip}">${label}</span>`;
}

// ====== 看板提示语 (2026-09-29) ======
// 目标读者是小学生, 但"你今天欠了 3 件事"这种表述不该出现在首页 ——
// 它只陈述缺口, 不给出路。改为两条原则:
//  1. 先报"还剩多少"再报"要多久": 光有数量无从下手, 带上剩余预估
//     分钟数, 孩子能判断"现在开始还来不来得及", 提示语才真正有用。
//  2. 语气随进度递进, 但只往"放松"方向递进, 不往"催促"方向递进:
//     剩得多时不粉饰(那是事实), 只给可执行的起点; 剩得少时把"就快完了"
//     明确说出来 —— 越接近终点越需要让人安心, 而不是越需要被催。
//     (2026-09-29 反馈: 原先的"冲一把 💪"太冲, 已改为"慢慢完成就好 🌿")
// isToday 必须由调用方传入: 看板可翻历史日期, 那时说"今天"是错的。
// done / skipped 也由调用方传入, 用来区分"真的做完了"和"跳过了"。
function boardHint({ isToday, pending, done = 0, skipped = 0, estRemaining }) {
  const dayWord = isToday ? '今天' : '这天';
  if (pending <= 0) {
    // 全部收尾。只剩"跳过"时说"太棒了"会让孩子觉得跳过也算完成,
    // 反而削弱了跳过按钮的分量 —— 这种情况只陈述"处理完了"。
    const skippedOnly = skipped > 0 && done === 0;
    return {
      cls: skippedOnly ? 'all-clear all-clear-dim' : 'all-clear',
      html: skippedOnly
        ? `${dayWord}的必做都处理完啦 ✓`
        : `${dayWord}的必做全部完成, 太棒了! 🎉`,
      // celebrate: 只有真做完才可庆祝 (2026-10-29)。全靠跳过收尾的
      // 那天没有"做完了"这回事, 不该弹庆祝横幅。
      celebrate: !skippedOnly,
    };
  }
  const timeNote = estRemaining > 0 ? `, 大约还要 <strong>${estRemaining}</strong> 分钟` : '';
  if (pending === 1) {
    return {
      cls: 'last-one',
      html: `就差最后 <strong>1</strong> 项啦${timeNote}, 慢慢完成就好 🌿`,
      celebrate: false,
    };
  }
  if (pending <= 3) {
    return {
      cls: 'almost',
      html: `还剩 <strong>${pending}</strong> 项${timeNote}, 加油! ✨`,
      celebrate: false,
    };
  }
  return {
    cls: 'many',
    html: `${dayWord}还有 <strong>${pending}</strong> 项${timeNote}, 一件一件来`,
    celebrate: false,
  };
}

// ====== 选做分组是否展开 (2026-10-29 用户反馈) ======
// 2026-09-28 曾要求选做默认展开, 理由是"家长看不到自己还欠着几件
// 选做"。那个理由只在还有欠账时成立: 一旦全做完, 展开着只是把
// "已经做完了"这件事反复铺在屏幕上, 让人以为还有事要做。
// 规则因此改成: 还有没做完的选做 → 展开 (沿用旧反馈); 全做完 →
// 折叠成一行, 摘要仍写清 "选做 (2) 2/2", 点开随时能查。
// 判据用 done 而非 settled: 跳过的选做仍可能被撤销, 而且摘要计数
// (done/总数) 满格才读作"做完了"。
function optionalGroupOpen(items) {
  return items.some(i => i.status !== 'done');
}

// 选做分组卡片。看板与日历详情共用这个渲染, 免得同一份数据在两个
// 界面表现不同 —— 家长会怀疑其中一边显示错了。
function optionalGroupCard(items, onChange) {
  const doneN = items.filter(i => i.status === 'done').length;
  const expandedAttr = optionalGroupOpen(items) ? ' open' : '';
  const card = el(`<details class="card optional-card"${expandedAttr}>` +
    `<summary class="card-title">选做 (${items.length})<span class="count">${doneN}/${items.length}</span></summary>` +
    `<div class="card-body"></div></details>`);
  items.forEach(item => card.querySelector('.card-body').appendChild(renderAssignment(item, true, onChange, _focusActualId)));
  return card;
}

// ====== 夸奖横幅 (2026-10-29 用户反馈) ======
// 全部完成时那句"太棒了"原先只是概览卡里一行小字 (--fs-caption),
// 手机上扫一眼就滑过去了 —— 而这恰恰是家长最想被看见的一句。
// 现在把它升级成独立横幅: 放在概览卡上方, 字号抬到 --fs-h2,
// 再加一个轻微的入场动画, 让"今天全部做完了"成为当天最显眼的一件事。
// 只有真做完才庆祝 (done>0); 全靠跳过收尾的那天不弹横幅, 否则等于
// 替"今天不做"发奖, 反而削弱跳过按钮的分量。
// 概览卡内的那一行在庆祝态下不重复渲染 —— 同一句话说两遍是噪音。
function hintBlock(hint) {
  if (!hint) return null;
  if (hint.celebrate) return el(`<div class="celebrate-banner">${hint.html}</div>`);
  return el(`<div class="board-hint ${hint.cls}">${hint.html}</div>`);
}

// ====== 各科剩余时间 (2026-10-09) ======
// 提示条只给一个总数("大约还要 110 分钟"), 家长看不出该先做哪一科,
// 也不好判断"语文那 40 分钟是不是太多了"。这里按科目拆开, 写法是
// "剩余 / 当天总量":
//     语文 20 / 50     还剩 20 分钟, 语文当天共估 50 分钟
// 分母不能省。孤零零一个 "语文还剩 20" 是个没有参照的数字: 20 分钟
// 既可能是"马上做完了", 也可能"才开了个头"。有了分母, 同一个 20
// 才能读出这门课今天的进度, 也顺带回答了"先做哪科"—— 分母大而
// 剩余小的科目已经接近收尾, 可以留到最后。
//
// 科目顺序原先在 loadBoard 里硬编码字面量, 这里是第二处用到它 ——
// 抽成 SUBJECT_ORDER 一处定义, 免得两处顺序不一致(顺序变了孩子会
// 以为数据在跳)。它与后端 config.SUBJECTS 顺序一致; 真要加科目,
// 三处(config / models.Subject / styles.css 类名)都得同步。
const SUBJECT_ORDER = ['语文', '数学', '英语'];

// M1 统一任务池: 培训/习惯任务没有科目, 用任务来源做标签。
const TASK_KIND_LABELS = { subject: '学科', training: '培训', habit: '习惯' };

// 行首标签: 学科作业仍是科目色块; 培训/习惯显示项目名 (没有项目时退回类型名)。
function taskTagHtml(item) {
  if (item.subject) {
    return `<span class="subject-tag ${item.subject}">${item.subject}</span>`;
  }
  const kind = item.task_kind || 'subject';
  const label = item.project_name || TASK_KIND_LABELS[kind] || '任务';
  return `<span class="subject-tag ${kind}" title="${escapeAttr(label)}">${escapeAttr(label)}</span>`;
}

// done 与 skipped 都算"已收尾"。抽成模块级函数而不是留在 loadBoard
// 里, 是为了让 subjectTimeStats 与提示条共用同一个判定 —— 这两处
// 显示的是同一份剩余时间, 一旦各自写一份, 迟早会算出两个数。
function isSettledItem(i) {
  return i.status === 'done' || i.status === 'skipped';
}

// 培训/习惯的剩余时间统计, 与 subjectTimeStats 同口径 (只算必做、只算未收尾)。
function kindTimeStats(byKind) {
  return ['training', 'habit'].flatMap(kind => {
    const items = (byKind && byKind[kind]) || [];
    const total = items.reduce((s, i) => s + (i.est_minutes || 0), 0);
    if (!total) return [];
    const remaining = items
      .filter(i => !isSettledItem(i))
      .reduce((s, i) => s + (i.est_minutes || 0), 0);
    return [{ label: TASK_KIND_LABELS[kind], className: kind, remaining, total }];
  });
}

// 各科剩余估时。口径与提示条的 estRemaining 严格同源, 两者能对上账:
//   只统计必做 (by_subject), 选做 (optional_items) 不在内 —— 与进度条
//     分母排除选做是同一个理由: 家长点过"选做"就不再是今天必须做的事。
//   剩余 = 未收尾 (todo/doing) 的估时之和。done/skipped 都算收尾, 所以
//     孩子做完或跳过后数字立刻下降。
// 没有作业的科目直接不返回: 硬凑一个 "英语 0 / 0" 只会占位置, 家长
// 还要分辨"0 估时"和"今天没英语"。
function subjectTimeStats(bySubject) {
  return SUBJECT_ORDER
    .map(subj => {
      const items = (bySubject && bySubject[subj]) || [];
      const total = items.reduce((s, i) => s + (i.est_minutes || 0), 0);
      const remaining = items
        .filter(i => !isSettledItem(i))
        .reduce((s, i) => s + (i.est_minutes || 0), 0);
      return { subject: subj, remaining, total };
    })
    .filter(s => s.total > 0);
}

// Toast 兜底 (设计文档 §9 坑 2)
let toastTimer = null;
function toast(msg, ms = 3000) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.remove('hidden');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), ms);
}

// ====== Tab 切换 + hash 路由 (2026-09-28) ======
// 每个菜单页有独立 URI, 方便直接打开/收藏/分享:
//   #/board 看板  #/entry 登记  #/list 日历
//   #/review 复盘  #/settings 设置
// 用 hash 而非 /board 真实路径: 无需后端配合、刷新不会 404,
// 且不会与将来的 /api/* 路由抢前缀。
//
// 复盘页原名 weekly (2026-10-30 改名 review): 该页除按周外还提供
// "本月", 名字里带 weekly 已与内容不符。
const TABS = ['board', 'entry', 'list', 'review', 'settings'];
const DEFAULT_TAB = 'board';

// 抑制 hashchange 回调里的再次写回, 否则 switchTab <-> hashchange
// 会互相触发形成死循环(表现是点菜单后页面瞬间白屏)。
let _syncingHash = false;

function tabFromHash() {
  const raw = String(location.hash || '').replace(/^#\/?/, '').trim();
  return TABS.includes(raw) ? raw : DEFAULT_TAB;
}

function switchTab(name, opts) {
  const tab = TABS.includes(name) ? name : DEFAULT_TAB;
  const updateUrl = !opts || opts.updateUrl !== false;
  // 浏览器前进/后退触发的调用不再写回 hash, 否则会把历史记录钉死,
  // 用户按返回键看似回到上一页, 实际又被我们推回同一个 tab。
  const syncUrl = updateUrl && !_syncingHash;
  if (syncUrl && tabFromHash() !== tab) {
    _syncingHash = true;
    try {
      history.pushState({ tab }, '', '#/' + tab);
    } finally {
      _syncingHash = false;
    }
  }

  document.querySelectorAll('#tabs button').forEach(b => {
    const active = b.dataset.tab === tab;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + tab));
  if (tab === 'board') {
    // 回到看板时重置到最新一天, 否则家长会停在几天前的那天上,
    // 以为"今天的作业怎么不见了"。
    _boardDate = null;
    _boardScroll = 0;
    loadBoard();
  }
  // 日历与详情一起加载。保留 _calYear/_calMonth, 切走再切回时
  // 家长仍停在上次翻到的那个月, 而不是被强行弹回本月。
  if (tab === 'list') {
    loadCalendar();
    if (_calSelected) loadList(_calSelected);
  }
  // 周计划保留上次翻到的那一周 (与日历保留月份同理), 不强行弹回本周。
  // 复盘页保留上次选的周期 (与日历页保留上次翻到的月份同理), 别把家长
  // 刚选好的「本月」又弹回「本周」。
  if (tab === 'review') setReviewPeriod(_reviewPeriod);
  if (tab === 'settings') loadSettings();
}
document.querySelectorAll('#tabs button').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// 前进/后退: 只切视图, 不写回 URL(见 switchTab 里 syncUrl 的说明)
function applyHashRoute() {
  _syncingHash = true;
  try {
    switchTab(tabFromHash(), { updateUrl: false });
  } finally {
    _syncingHash = false;
  }
}
window.addEventListener('hashchange', applyHashRoute);
window.addEventListener('popstate', applyHashRoute);
window.switchTab = switchTab;

// ====== Health ======
// 右上角主显示"当天日期 + 星期"(2026-09-28 用户反馈: 原来显示
// "DB ok · 模型 MiniMax-M3" 是给开发看的, 家长更关心今天是几号)。
// AI 可用性降为次要信息: 可用时不占视觉, 不可用时才提示 —— 因为
// "AI 未配置"是需要提醒的故障, 静默丢掉会让家长以为功能坏了。
//
// 复用 todayStr() 而不是自己再拼一遍日期: 它已经处理了 UTC+8
// 凌晨会退到前一天的问题, 两套实现迟早会漂移。
function renderToday() {
  const now = new Date();
  const week = '日一二三四五六'[now.getDay()];
  return {
    date: todayStr(),
    week: `星期${week}`,
    short: `${now.getMonth() + 1}月${now.getDate()}日 星期${week}`,
  };
}

async function loadHealth() {
  const bar = document.getElementById('health-bar');
  const t = renderToday();
  try {
    const h = await api('GET', '/health');
    const aiOk = !!h.ai_reachable;
    // 主: 日期 + 星期。AI 正常时完全不提, 异常时才补一句警告。
    bar.innerHTML = `<span class="today-label">${t.short}</span>` +
      (aiOk ? '' : `<span class="health-warn"> · AI 未配置, 可手动添加</span>`);
    bar.className = aiOk ? 'ok-true' : 'ok-false';
    // 手动添加按钮 AI 不可用时加暗红描边 (设计文档 §9 坑 4)
    const btnManual = document.getElementById('btn-add-manual');
    if (btnManual) {
      btnManual.setAttribute('data-ai-available', h.ai_reachable ? 'true' : 'false');
      if (!h.ai_reachable) {
        btnManual.title = 'AI 暂不可用, 你可以手动添加';
      } else {
        btnManual.title = '手动添加一条作业';
      }
    }
  } catch (e) {
    // 失败时也要显示日期 —— 今天是几号跟后端通不通无关, 不能被异常冲掉。
    bar.innerHTML = `<span class="today-label">${t.short}</span>` +
      `<span class="health-warn"> · 连接异常</span>`;
    bar.className = 'ok-false';
  }
}

// ====== 看板 ======
// 全量重建 DOM 会把滚动位置弹回顶部, 家长点一下状态就得重新往下滚。
// 重建前后记住 scrollTop 并还原。
let _boardScroll = 0;
// 当前看的是哪一天 (null = 最近一个有作业的日期, 由后端决定)。
// 记录它是为了让"录入后跳回看板"仍然落在用户刚看的那一天上。
let _boardDate = null;

async function loadBoardDateTabs() {
  const box = document.getElementById('board-date-tabs');
  try {
    const r = await api('GET', '/board/dates?limit=3');
    const dates = r.dates || [];
    if (!dates.length) { box.innerHTML = ''; return []; }
    // 只有一个日期时不必显示 Tab —— 没有可切换的余地, 反而占视觉空间
    if (dates.length < 2) { box.innerHTML = ''; return dates; }

    const active = _boardDate || dates[0].date;
    box.innerHTML = '';
    dates.forEach(d => {
      // 分段控件 (2026-10-12 密度优化): 原来每张卡是一个 84px 宽的
      // 竖排方块 (标题在上、进度在下), 三个就占掉约 90px 高。现在改成
      // 横排: 标题与进度同一行, 整条高度压到 30px 左右。
      const b = el(`
        <button class="date-tab ${d.date === active ? 'active' : ''}" data-date="${d.date}">
          <span class="dt-label">${d.label}</span><span class="dt-count">${d.done}/${d.total}</span>
        </button>
      `);
      b.addEventListener('click', () => {
        if (d.date === _boardDate) return;
        _boardDate = d.date;
        _boardScroll = 0;      // 换日期后回到顶部, 否则会停在新日期的半截位置
        loadBoard();
      });
      box.appendChild(b);
    });

    // 假期提示: 今天这张卡没有作业, 说明这几天是假期/无作业。
    // 否则家长会困惑"明明今天有课, 怎么一条作业都没有"。
    // 注意判据是 has_data 而不是 dates[0].date —— 后端现在总会把"今天"
    // 放在第一位, 用日期比就会永远不触发。
    const t = dates.find(d => d.is_today);
    if (t && !t.has_data) {
      box.appendChild(el(`<span class="date-tab-hint">今天没有作业, 下面是最近有记录的日子</span>`));
    }
    return dates;
  } catch (e) {
    box.innerHTML = '';
    return [];
  }
}

async function loadBoard() {
  const root = document.getElementById('board-content');
  const scroller = root.closest('.view.active') || document.scrollingElement;
  _boardScroll = scroller ? scroller.scrollTop : 0;
  root.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const b = await api('GET', _boardDate ? `/board?date=${_boardDate}` : '/board/today');
    // 首次进入时锁定到后端返回的实际日期, 避免重复请求同一天。
    // 必须在渲染 Tab 之前定下来: Tab 的高亮跟着 _boardDate 走, 先画 Tab
    // 再赋值会让深色底停在"第一张"而不是正文真正展示的那一天。
    if (!_boardDate) _boardDate = b.date;
    // M4 训练打卡: 看板一次带回当天每条任务的打卡记录 (键为 assignment_id),
    // 挂到 item 上让行内直接渲染"练了多久 / 几组 × 几次", 不必逐行请求。
    attachLogs((b.by_kind || {}).training);
    attachLogs((b.by_kind || {}).habit);
    attachLogs(b.optional_items);
    await loadBoardDateTabs();
    root.innerHTML = '';
    if (b.is_late_night) {
      root.appendChild(el('<p class="muted">已是凌晨, 注意休息</p>'));
    }
    if (!b.has_data) {
      // 空状态 (设计文档 §7.6)
      // "今天没有作业"是坏消息式的空态, 但对小孩来说"今天不用做作业"
      // 本身就是好消息 —— 措辞按"没有作业"这个事实来庆祝, 而不是
      // 显示一个像是丢了东西的空状态。
      root.appendChild(el(`
        <div class="empty">
          <div class="empty-icon">${b.is_today ? '🌈' : '📋'}</div>
          <p>${b.is_today ? '今天没有作业, 好好休息一下' : '这天没有作业记录'}</p>
          ${b.is_today ? '<button class="primary" data-action="goto-entry">去录入</button>' : ''}
          <p class="muted">${b.is_today ? '老师还没发通知, 或者手动添加一条' : '可以在「日历」页查看其他日期'}</p>
        </div>
      `));
      const go = root.querySelector('[data-action="goto-entry"]');
      if (go) go.addEventListener('click', () => switchTab('entry'));
      // 没作业的日子也能记心情: 孩子今天状态如何, 与做没做作业无关。
      // 只限今天 —— 翻历史日期时不该出现"今晚小结"。
      return;
    }
    // 进度条 (2026-09-28 口径: 跳过算已收尾, 分母排除选做)
    const p = b.progress;
    const pct = p.pct != null ? p.pct : 0;
    const skippedNote = p.skipped ? ` · 跳过 ${p.skipped}` : '';
    // 选做写成 x/y 而不是光一个总数: "选做 1"到底是 1 项还是 1 项做完,
    // 原来分不清, 家长只能自己数下面的卡片。分母是选做总数, 与必做
    // 那侧的 "已收尾 x / 必做 y" 读法保持一致。
    const optDone = p.optional_done || 0;
    const optNote = p.optional ? ` · 选做 ${optDone}/${p.optional}` : '';

    // 分区: 待完成 / 已收尾 / 选做(折叠)
    // 口径说明: done 与 skipped 都算"已收尾", 但只有 done 算"完成"
    const all = [];
    for (const subj of SUBJECT_ORDER) all.push(...(b.by_subject[subj] || []));
    // 培训/习惯任务同样计入"待完成/已收尾"与进度提示, 不能只渲染学科。
    for (const kind of ['training', 'habit']) all.push(...((b.by_kind || {})[kind] || []));
    const pending = all.filter(i => !isSettledItem(i));
    const settled = all.filter(i => isSettledItem(i));

    // 提示语 (2026-09-29 改为分档鼓励语, 原为"今日还有 N 项要做")
    // 口径提示: all 只含必做(by_subject), 选做不在内, 与进度条分母一致。
    // 剩余预估只累加"还没收尾"的条目 —— 已完成的那部分不该再计进待做时间,
    // 否则提示条会一直显示当天的总估时, 孩子做完后数字却不降, 反而更泄气。
    const estRemaining = pending.reduce((s, i) => s + (i.est_minutes || 0), 0);
    const hint = (pending.length || all.length) ? boardHint({
      isToday: b.is_today,
      pending: pending.length,
      done: settled.filter(i => i.status === 'done').length,
      skipped: settled.filter(i => i.status === 'skipped').length,
      estRemaining,
    }) : null;

    // 各科剩余时间 (2026-10-09): 把提示语那个总数按科目拆开。
    // 全部收尾后仍要显示 (语文 0 / 40): 家长确认"确实做完了"需要
    // 看到这个 0, 空条只会让人怀疑是不是加载坏了。
    const subjStats = subjectTimeStats(b.by_subject).map(s => ({
      label: s.subject, className: s.subject, remaining: s.remaining, total: s.total,
    })).concat(kindTimeStats(b.by_kind));

    // ====== 概览卡 (2026-10-12 密度优化) ======
    // 原先是四块独立内容纵向堆叠: 进度卡 / 截止提醒 / 鼓励横幅 /
    // 学科用时条 —— 在 375px 屏上合计约 300px, 占掉首屏一半, 一屏只看得到
    // 三四条作业。合并成一张卡: 第一行收尾进度 + 总用时, 第二行学科
    // 色块, 鼓励语降级成一行小字贴在进度条上。
    // 进度数字仍是全卡最大 —— 家长扫一眼要的就是"今天做到哪了"。
    const subjChips = subjStats.map(s => {
      const done = s.remaining === 0;
      return `<span class="subj-time ${s.className}${done ? ' is-done' : ''}">` +
        `<span class="st-name">${s.label}</span>` +
        `<span class="st-num"><strong>${s.remaining}</strong><span class="st-sep">/</span>${s.total}</span>` +
        `</span>`;
    }).join('');

    // 夸奖横幅 (2026-10-29): 全部做完时从概览卡里提出来, 独立成一条
    // 横幅放在卡片上方 —— 概览卡是"信息", 横幅是"事件", 后者不该被前者
    // 压成一行小字。庆祝态下卡内不再重复渲染那句提示。
    const hintEl = hintBlock(hint);
    if (hintEl) root.appendChild(hintEl);

    root.appendChild(el(`
      <div class="progress-bar">
        <div class="progress-text">
          <span>${b.label === '今天' ? '' : b.label + ' · '}已收尾 <strong>${p.settled}</strong> / 必做 <strong>${p.required}</strong> · ${pct}%</span>
          <span class="effort-total"><strong class="et-num">${p.actual_total || 0}</strong><span class="et-unit">分钟</span></span>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:${pct}%;"></div></div>
        ${hint && !hint.celebrate ? `<div class="board-hint ${hint.cls}">${hint.html}</div>` : ''}
        ${subjChips ? `<div class="subj-time-row">${subjChips}</div>` : ''}
        ${optNote || skippedNote ? `<div class="progress-sub">完成 ${p.done}${skippedNote}${optNote} · 估时 ${p.est_total || 0} 分</div>` : ''}
      </div>
    `));

    // 截止时间提醒 (设计文档 §7.1 第 2 块)
    // 留在概览卡之外: 它是"异常提示"而不是"日常概览", 全完成的那天不该
    // 出现, 混进卡片里会让家长误以为每天都有内容。
    if (b.upcoming_deadlines && b.upcoming_deadlines.length) {
      const items = b.upcoming_deadlines.map(d =>
        `${taskTagHtml(d)}
         ${d.task_name} · 截止 ${fmtDateTime(d.deadline_time)}`
      ).join('<br/>');
      root.appendChild(el(`<div class="deadline-warn"><strong>⚠ 未来 7 天到期</strong>${items}</div>`));
    }

    // 已逾期 (M2 执行台): 前几天的任务错过截止后原本会从提醒区彻底消失,
    // 家长不翻回那一天就永远看不到。放在截止提醒之后、科目卡片之前 ——
    // 它比"未来要做"更紧急, 但今天卡片里的行内标记已经覆盖当日逾期,
    // 所以这里只列往期, 不重复。
      // 任务名可点: 提醒只说"有一条逾期了", 若不给出处理入口, 家长还
      // 得自己去日历上翻是哪一天。点一下直接切到那天的看板, 在那里
      // 完成 / 顺延 / 跳过, 闭环才算走完。
        `${taskTagHtml(d)}
      ).join('<br/>');
        const goto = () => { _boardDate = link.dataset.date; loadBoard(); };
        link.addEventListener('click', goto);
        link.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goto(); }
        });
      });
      root.appendChild(box);
    }

    const section = (title, items, cls, open) => {
      if (!items.length) return;
      const doneN = items.filter(i => i.status === 'done').length;
      // 已收尾/选做默认折叠 (2026-10-12): 这两块是"回看"用的, 而回看
      // 是低频动作; 默认展开时它们把待完成区挤出首屏, 而后者才是家长
      // 每次打开就要看的。用 details/summary 而不是删掉入口 —— 收起
      // 换来的必须是"按需可查", 而不是"看不到"。
      const card = open
        ? el(`<div class="card ${cls || ''}"><div class="card-title">${title}<span class="count">${doneN}/${items.length}</span></div><div class="card-body"></div></div>`)
        : el(`<details class="card ${cls || ''}"><summary class="card-title">${title}<span class="count">${doneN}/${items.length}</span></summary><div class="card-body"></div></details>`);
      items.forEach(item => card.querySelector('.card-body').appendChild(renderAssignment(item, true, null, _focusActualId)));
      root.appendChild(card);
    };
    section('待完成', pending, 'sec-pending', true);
    section('已收尾', settled, 'sec-settled', false);

    // 选做分组 (2026-09-28 用户反馈: 默认展开; 2026-10-29: 全做完则折叠)
    // 原先默认收起, 理由是"低优先级不该稀释主任务视觉权重"。但收起后
    // 家长根本看不到自己还欠着几件选做, 反复要点开才安心 —— 隐藏在这里
    // 换来的不是"聚焦", 而是"不确定"。既然底色已改为浅灰(不再抢焦点),
    // 默认展开就不再有稀释问题, 收起与不收起的信息成本对比很明确。
    // 2026-10-29 追加: 选做全做完之后不再展开 —— 那一屏已经没有欠账,
    // 摊开只剩"已完成"的重复信息, 还把首屏往下挤。全做完时折叠成一行
    // 摘要 (选做 2/2), 入口仍在, 家长想回看随时点开。
    if (b.optional_items && b.optional_items.length) {
      root.appendChild(optionalGroupCard(b.optional_items, null));
    }
    // 晚间小结 (M3 日结): 只放在"今天", 是每晚的固定收尾动作。
  } catch (e) {
    root.innerHTML = `<div class="error-banner">看板加载失败: ${e.message}</div>`;
  }
  if (scroller) scroller.scrollTop = _boardScroll;
}

// 三问用固定档位按钮而不是滑杆/原生 select:
//   - 家长晚上已经累了, 三个表情点一下完事;
//   - 固定档位才能被周报按"难度=难 / 专注=差 / 情绪=烦"聚合, 滑杆还要分桶。
// 1–2 年级由后端 mode=lite 少问一题(专注), 前端只按返回的 mode 渲染。
const DW_FOCUS = [['good', '很专心'], ['ok', '一般'], ['bad', '容易分心']];
const DW_MOOD = [['good', '开心'], ['ok', '平静'], ['bad', '有点烦']];

  root.appendChild(box);
}

  try {
  } catch (e) {
    // 小结失败不该把整张看板拖垮: 它只是收尾卡片, 静默隐藏即可。
    box.innerHTML = '';
  }
}

  const btns = options.map(([v, text]) =>
    `<button type="button" class="dw-choice${selected === v ? ' active' : ''}" ` +
    `data-q="${q}" data-v="${v}" aria-pressed="${selected === v}">${text}</button>`
  ).join('');
  return `<div class="dw-q"><span class="dw-q-label">${label}</span>` +
    `<div class="dw-choices">${btns}</div></div>`;
}

  const s = w.summary || {};
  const r = w.reflection || {};
  const lite = w.mode === 'lite';
  // 选中态放在闭包里而不是读 DOM: 重绘整卡后 DOM 会换掉,
  // 保存时必须以"用户最后点的"为准, 而不是重绘前的旧节点。
  const selected = {
    difficulty: r.difficulty || null,
    focus: r.focus || null,
    mood: r.mood || null,
  };
  const suggestions = (w.suggestions || [])
    .map(t => `<li>${escapeAttr(t)}</li>`).join('');
  const card = el(`
      <div class="card-title">今晚小结<span class="count">1 分钟知道今天</span></div>
      <div class="dw-headline">${escapeAttr(w.headline || '')}</div>
      <div class="dw-stats">
        <span>完成 <strong>${s.done || 0}</strong>/<strong>${s.required || 0}</strong></span>
        <span>用时 <strong>${s.actual_total || 0}</strong> 分</span>
      </div>
      <ul class="dw-suggest">${suggestions}</ul>
      <div class="dw-questions">
      </div>
      <input class="dw-note" maxlength="200" aria-label="今晚备注"
             placeholder="想记一句？比如：数学卡在应用题"
             value="${escapeAttr(r.note || '')}" />
      <div class="dw-save-row">
        <button type="button" class="primary dw-save">${w.reflection ? '更新小结' : '保存小结'}</button>
        <span class="dw-saved muted">${w.reflection ? '今晚已记录' : '难度和情绪选一下就能保存'}</span>
      </div>
    </div>
  `);

  card.querySelectorAll('.dw-choice').forEach(btn => {
    btn.addEventListener('click', () => {
      const q = btn.dataset.q;
      selected[q] = btn.dataset.v;
      card.querySelectorAll(`.dw-choice[data-q="${q}"]`).forEach(b => {
        const on = b.dataset.v === selected[q];
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    });
  });

  card.querySelector('.dw-save').addEventListener('click', async () => {
    if (!selected.difficulty || !selected.mood) {
      toast('先选一下今天的难度和情绪');
      return;
    }
    const payload = {
      date: w.date,
      difficulty: selected.difficulty,
      mood: selected.mood,
      note: card.querySelector('.dw-note').value.trim() || null,
    };
    // 低年级两问版没有专注一题; 用户在普通模式没点专注时也不强制。
    if (!lite && selected.focus) payload.focus = selected.focus;
    try {
      toast('已记录今晚小结');
    } catch (e) {
      toast('保存失败: ' + e.message);
    }
  });

  box.innerHTML = '';
  box.appendChild(card);
}

// F18: 练琴/体育的 30 秒打卡 —— 时长 + 组数/次数 + 曲目段落。
// 只对 task_kind=training 开放: 学科作业的"完成"走状态接口, 习惯打卡
// 是另一条范围, 混进来会让打卡数据失去"训练"这个限定。
//
// 打卡即完成 (后端同一事务写记录 + 置 done + 填实耗), 所以界面上训练
// 任务不出现普通「完成」按钮 —— 两种情况都不该并存, 否则家长会以为
// "不打卡也能收尾", 训练周报就没数据了。
function trainingLogSummary(log) {
  const parts = [];
  if (log.duration_minutes != null) parts.push(`${log.duration_minutes} 分钟`);
  if (log.sets != null && log.reps != null) parts.push(`${log.sets} 组 × ${log.reps} 次`);
  else if (log.sets != null) parts.push(`${log.sets} 组`);
  else if (log.reps != null) parts.push(`${log.reps} 次`);
  if (log.segment) parts.push(log.segment);
  return parts.join(' · ');
}

  if (_modalCloser) return;                 // 已有弹窗在等确认, 别覆盖它
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent =
    `训练打卡 · ${item.project_name || '训练'}`;
  // 时长默认取预估: 家长点开弹窗最可能就是想确认"今天练了 30 分钟",
  // 预填好只需确认一下, 比每次从零输入快得多。
  const dur = log ? log.duration_minutes : (item.est_minutes != null ? item.est_minutes : 15);
  document.getElementById('modal-content').innerHTML = `
    <div class="form-grid">
      <label>
        <span class="form-label">练习时长</span>
        <span class="form-field">
          <span class="form-hint">分钟; 打卡后任务自动记为完成</span>
        </span>
      </label>
      <label>
        <span class="form-label">曲目 / 段落</span>
        <span class="form-field">
          <input id="tl-segment" maxlength="60" placeholder="如 第 2 段 / 音阶 C 大调"
                 value="${log && log.segment ? escapeAttr(log.segment) : ''}" />
          <span class="form-hint">练琴记曲目段落, 体育可留空</span>
        </span>
      </label>
      <label>
        <span class="form-label">组数</span>
        <input id="tl-sets" type="number" min="0" max="100" inputmode="numeric" placeholder="选填"
               value="${log && log.sets != null ? log.sets : ''}" />
      </label>
      <label>
        <span class="form-label">每组次数</span>
        <input id="tl-reps" type="number" min="0" max="1000" inputmode="numeric" placeholder="选填"
               value="${log && log.reps != null ? log.reps : ''}" />
      </label>
      <label>
        <span class="form-label">备注</span>
        <input id="tl-note" maxlength="200" placeholder="选填, 如 今天有点抗拒"
               value="${log && log.note ? escapeAttr(log.note) : ''}" />
      </label>
    </div>`;
  m.classList.remove('hidden');
  // 选填数字框的读取: 空 -> null, 越界 -> 留在弹窗报错 (与后端 CHECK 同口径)。
  const readOptional = (id, min, max, label) => {
    const raw = document.getElementById(id).value.trim();
    if (!raw) return { ok: true, value: null };
    const n = parseInt(raw, 10);
    if (!Number.isFinite(n) || n < min || n > max) {
      toast(`${label}需在 ${min}~${max} 之间`);
      return { ok: false };
    }
    return { ok: true, value: n };
  };
  setModalActions({
    mode: 'confirm',
    okText: log ? '更新打卡' : '打卡',
    onOk: async () => {
      if (!Number.isFinite(duration) || duration < 0 || duration > 240) {
        toast('练习时长需在 0~240 分钟');
        return;
      }
      const sets = readOptional('tl-sets', 0, 100, '组数');
      if (!sets.ok) return;
      const reps = readOptional('tl-reps', 0, 1000, '每组次数');
      if (!reps.ok) return;
      const okBtn = document.getElementById('modal-ok');
      okBtn.disabled = true;                 // 请求锁: 防连点提交两次
      try {
        await api('POST', '/training-logs', {
          assignment_id: item.id,
          duration_minutes: duration,
          sets: sets.value,
          reps: reps.value,
          segment: document.getElementById('tl-segment').value.trim() || null,
          note: document.getElementById('tl-note').value.trim() || null,
        });
        closeModal();
        toast(log ? '打卡已更新' : '已打卡, 任务记为完成');
        await refresh();
      } catch (e) {
        okBtn.disabled = false;
        toast('打卡失败: ' + e.message);
      }
    },
  });
}

// 渲染任务行 (设计文档 §5.1 + §9 4 个坑)
// onChange: 状态变更后的回调。看板传 loadBoard, 历史日历传刷新日历+详情,
// 不传则按钮不重绘 —— 历史页若复用 loadBoard 会把用户弹出日历页。
// focusActualFor: 仅当等于 item.id 时, 重绘后自动聚焦这条的"填实际用时"。
// 由"刚点完成"的那次点击设置 (见 patchStatus), 渲染完即清空 ——
// 不能无差别自动聚焦, 否则翻看历史日期也会被抢走焦点并滚到底部。
let _focusActualId = null;

function renderAssignment(item, withStatus, onChange, focusActualFor) {
  // pending_actual: done 但未填实耗时 (设计文档 §9 坑 2)
  const isPendingActual = item.status === 'done' && item.actual_minutes == null;
  const confClass = `confidence-${item.confidence}`; // 给整行加边条 / 底色
  // 选做行加暖色底 + 左侧竖线, 与必做行区分 (2026-09-28)
  const optClass = item.is_optional ? 'is-optional' : '';
  const row = el(`
    <div class="assignment-row ${item.status} ${confClass} ${optClass} ${isPendingActual ? 'pending-actual' : ''}" data-id="${item.id}">
      <div class="row-main">
        ${taskTagHtml(item)}
        <div style="flex:1;">
          <div class="task-name">${escapeAttr(item.task_name)}${item.is_optional ? '<span class="optional-tag">选做</span>' : ''}</div>
        </div>
        ${item.est_minutes != null
          ? `<div class="est-min"><span class="est-label">预估</span>${item.est_minutes}</div>`
          : '<div class="est-min muted">—</div>'}
      </div>
      <div class="row-foot">
        <div class="row-meta">
          ${item.deadline_time ? `<span>⏰ ${fmtDateTime(item.deadline_time)}</span>` : ''}
          ${item.task_type ? `<span>类型: ${item.task_type}</span>` : ''}
          ${confidencePill(item.confidence)}
          ${item.source_span ? `<span class="source-link" data-action="show-source">原文</span>` : ''}
          ${item.actual_minutes != null ? `<span class="actual-tag">实际用时 <strong>${item.actual_minutes}</strong> 分</span>` : ''}
        </div>
        ${withStatus ? '<div class="row-actions"></div>' : ''}
      </div>
    </div>
  `);

  if (!withStatus) return row;

  const actions = row.querySelector('.row-actions');
  // 看板与历史页的刷新方式不同, 统一走回调, 避免历史页误触发 loadBoard
  const refresh = onChange || (() => loadBoard());

  // 两个终结动作: 完成 / 今天不做
  // 2026-09-28 用户反馈: "开始"按钮是多余的 —— 家长不会特意去点"开始",
  // 直接点"完成"即可。故移除 doing 的入口, doing 状态仅可能来自历史数据,
  // 下面的"完成"分支仍兼容它, 保证老数据可被正常收尾。
  const patchStatus = async (s, btn) => {
    btn.disabled = true;                       // 请求锁: 防快速连点产生并发 PATCH
    try {
      await api('PATCH', `/assignments/${item.id}/status`, { status: s });
      if (s === 'done' && item.actual_minutes == null) {
        toast(item.est_minutes != null
          ? `已标记完成, 用时已按预估 ${item.est_minutes} 分钟填好, 可直接保存`
          : '已标记完成, 建议补填实际用时 (不填则按预估时间统计)');
        // 只有"刚点完成"这一条才允许重绘后自动聚焦用时框。
        // 通过回调把 id 传下去, 刷新时才知道该聚焦谁。
        _focusActualId = item.id;
      } else if (s === 'skipped') {
        toast('已跳过, 不计入完成数');
      }
      await refresh();
      _focusActualId = null;
    } catch (e) {
      btn.disabled = false;
      toast('状态更新失败: ' + e.message);      // 用 toast 而非 alert, 不打断流程
    }
  };

  // 完成: todo/doing -> done
  // 2026-09-29 用户要求把绿底"完成"改成小红花。小红花是这个年龄段的
  // 奖励隐喻, 比抽象的绿色按钮更能传达"做对了"。但纯图标会丢掉可点击
  // 的文字线索, 故图标在前、文字在后: 小孩认花形, 家长扫文字。
  if (item.status === 'todo' || item.status === 'doing') {
    if (item.task_kind === 'training') {
      // 训练任务 (M4): 打卡取代「完成」。打卡会一并记录时长/组数/段落,
      // 再留一个纯「完成」按钮会让人以为不打卡也能收尾 —— 那样训练线
      // 周报就没有数据可写, 打卡率也无从统计。
      const b = el('<button class="status-btn training" aria-label="训练打卡">打卡</button>');
      actions.appendChild(b);
    } else {
    const b = el(`<button class="status-btn done" aria-label="标记为已完成">` +
      `<svg class="flower" viewBox="0 0 24 24" aria-hidden="true" focusable="false">` +
      `<use href="#i-flower"></use></svg><span>完成</span></button>`);
    b.addEventListener('click', () => patchStatus('done', b));
    actions.appendChild(b);
    }
  } else if (item.status === 'done') {
    // 已打卡的训练: 最常做的是修正记录, 给「改打卡」; 撤回仍走下面的「重做」
    // (后端在 done->todo 时会连打卡记录一起删, 两者语义一致)。
    if (item.task_kind === 'training') {
      const b = el('<button class="status-btn training" aria-label="修改打卡记录">改打卡</button>');
      actions.appendChild(b);
    }
    // 重做: done -> todo。
    // 点错一次就必须能改, 否则家长只能去数据库里手改。
    // 注意后端在 done -> 非 done 时会清空 actual_minutes,
    // 所以这项会退回"待补实际用时"状态, 属预期行为。
    // 花朵换成灰调勾选态: 它已经是"送出去的花", 现在提供的是撤回入口,
    // 不该再用鲜红去吸引注意力 —— 和"完成"的正向诱惑拉开距离。
    const b = el(`<button class="status-btn redo" aria-label="撤销完成, 重新标记为待完成">` +
      `<svg class="flower" viewBox="0 0 24 24" aria-hidden="true" focusable="false">` +
      `<use href="#i-flower-done"></use></svg><span>重做</span></button>`);
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        await api('PATCH', `/assignments/${item.id}/status`, { status: 'todo' });
        toast('已撤销完成, 这条回到待完成');
        await refresh();
      } catch (e) { b.disabled = false; toast('操作失败: ' + e.message); }
    });
    actions.appendChild(b);
  }
  // 今天不做: 未收尾的项才需要; 已跳过的项给"撤销"
  if (item.status === 'todo' || item.status === 'doing') {
    const b = el(`<button class="status-btn skipped" aria-label="今天不做这条">今天不做</button>`);
    b.addEventListener('click', () => patchStatus('skipped', b));
    actions.appendChild(b);
  } else if (item.status === 'skipped') {
    const b = el(`<button class="status-btn undo" aria-label="撤销跳过">撤销跳过</button>`);
    b.addEventListener('click', () => patchStatus('todo', b));
    actions.appendChild(b);
  }
  // 选做: is_optional 双向开关 (后端 PATCH 接口此前从未被前端调用过)
  if (item.is_optional) {
    const b = el(`<button class="status-btn opt-toggle on" aria-label="取消选做标记">取消选做</button>`);
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        await api('PATCH', `/assignments/${item.id}/optional`, { is_optional: false });
        toast('已取消选做, 计入今日必做');
        await refresh();
      } catch (e) { b.disabled = false; toast('操作失败: ' + e.message); }
    });
    actions.appendChild(b);
  } else {
    const b = el(`<button class="status-btn opt-toggle" aria-label="标记为选做">选做</button>`);
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        await api('PATCH', `/assignments/${item.id}/optional`, { is_optional: true });
        toast('已标记选做, 不计入今日必做');
        await refresh();
      } catch (e) { b.disabled = false; toast('操作失败: ' + e.message); }
    });
    actions.appendChild(b);
  }

  // pending_actual 实际用时输入 (设计文档 §9 坑 2 — 高亮 + 自动 focus)
  //
  // 2026-10-12 密度优化: 输入框改为按需展开。此前 done 但未填用时的条目
  // 会常驻显示「输入框 + 保存 + 跳过」三个控件, 在 375px 屏上单这一条就占
  // 近 1/4 屏 —— 而"待补用时"是少数状态, 让它长期占位不划算。
  //
  // 没有改成弹窗: 家长点「完成」的那一刻就是要填用时, 弹窗会多一次点击
  // 并丢掉"这是哪一条"的上下文。所以默认只留一个「补用时」按钮, 点它
  // 就地展开; 刚点完成的那条 (focusActualFor) 仍自动展开并聚焦 ——
  // 那正是设计 §9坑 2 的意图, 不该因为"默认收起"而被牺牲。
  if (isPendingActual) {
    const fillRow = el('<div class="actual-fill"></div>');
    const inputWrap = el('<div class="row actual-fill-row"></div>');
    const input = el(`<input type="number" class="actual-input" placeholder="填实际用时" min="0" max="240" inputmode="numeric" />`);
    const saveBtn = el('<button class="primary">保存</button>');
    const skipBtn = el('<button class="ghost">跳过</button>');
    saveBtn.addEventListener('click', async () => {
      const v = parseInt(input.value, 10);
      if (isNaN(v) || v < 0 || v > 240) {
        input.classList.add('error');
        toast('用时需在 0~240 分钟之间');
        return;
      }
      try {
        await api('PATCH', `/assignments/${item.id}/completion`, { actual_minutes: v });
        toast('已记录');
        refresh();
      } catch (e) { toast('保存失败: ' + e.message); }
    });
    skipBtn.addEventListener('click', () => {
      input.classList.remove('error');
      input.value = '';
      input.placeholder = '不填则按预估时间统计';
      toast('本次不记录用时, 之后仍可补填');
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveBtn.click(); });
    inputWrap.append(input, saveBtn, skipBtn);

    // 触发按钮: 文案说明动作, 不用图标 —— "补用时"三个字比一个沙漏
    // 更难误解, 而这里正是需要被看懂的一步。
    //
    // 它同时是展开/收起的切换: 展开后按钮不消失, 只换成「取消」, 点它
    // 回到紧凑态。初版是展开时把按钮删掉换输入行, 结果展开后就再没有
    // 返回路径, 家长只能刷新页面 (2026-10-12 用户反馈)。
    // 「跳过」是"这次不记录", 与「取消」的"关掉这个面板"语义不同,
    // 两者都要留: 前者会清输入并提示, 后者只是收起。
    const fillBtn = el(`<button class="status-btn fill-actual">补用时</button>`);
    let expanded = false;
    const expand = () => {
      expanded = true;
      fillBtn.textContent = '取消';
      fillRow.appendChild(inputWrap);
      // 预填预估值 (2026-10-14 用户反馈): 家长点「完成」时心里已有的
      // 默认答案就是预估, 让他从空框里敲一遍同样的数字是纯多余动作 ——
      // "一般用时准的话我是不手动填写的"。填好并全选: 直接回车/点保存
      // 就走完, 想改则选中态下敲数字即覆盖, 不用先删。
      // 不自动提交: 预估值是 AI 猜的, 静默当成实测值会污染周复盘的
      // "预估 vs 实际"偏差统计 —— 偏差本来就靠"没填=按预估"体现。
      if (item.est_minutes != null) input.value = item.est_minutes;
      input.focus();
      input.select();
    };
    const collapse = () => {
      expanded = false;
      fillBtn.textContent = '补用时';
      input.classList.remove('error');
      input.value = '';
      fillRow.replaceChildren();      // 清空容器 -> CSS 的 :not(:empty) 恢复 display:contents, 行高回落
      fillBtn.focus();                // 焦点交还按钮: 否则它会掉到 body, 键盘用户下一步得从头 Tab
    };
    fillBtn.addEventListener('click', () => (expanded ? collapse() : expand()));
    actions.appendChild(fillBtn);
    actions.appendChild(fillRow);

    // 只在"家长刚点了这一条的完成"时才自动展开并 focus。
    // 不能对每条待补用时的作业都自动展开 —— 否则只是翻看某一天的
    // 历史作业, 也会被50ms 后抢走焦点并把页面滚到底部 (2026-09-29
    // 用户反馈), 紧接着敲的键还会落进这个输入框。
    if (focusActualFor === item.id) {
      expand();
      setTimeout(() => input.focus(), 50);
    }
  } else if (item.actual_minutes != null) {
    // 已填, 给修改入口
    const editBtn = el('<button class="ghost">改用时</button>');
    editBtn.addEventListener('click', async () => {
      const n = await promptNumber({
        title: '修改实际用时',
        label: `实际用时 (${item.task_name})`,
        value: item.actual_minutes,
        min: 0,
        max: 240,
        hint: '0 ~ 240 分钟; 周复盘的"预估 vs 实际"按这个数统计。',
      });
      if (n === null) return;                        // 取消 / Esc: 不动原有记录
      editBtn.disabled = true;
      try {
        await api('PATCH', `/assignments/${item.id}/completion`, { actual_minutes: n });
        toast(`已改为 ${n} 分钟`);
        await refresh();
      } catch (e) {
        editBtn.disabled = false;
        toast('保存失败: ' + e.message);
      }
    });
    actions.appendChild(editBtn);
  }

  // 编辑 —— 改这条作业本身的字段 (描述/估时/日期/类型), 不是改它的状态。
  //
  // 放在「删除」之前而不是行首: 行内已有"完成/重做/今天不做/选做"四个
  // 状态类操作, 编辑是内容纠错, 与它们语义不同, 排在一起容易误点。
  //
  // 所有状态都给了入口 (含 done/skipped): 已完成的那条若描述写错了,
  // 同样需要能改 —— 而删除对 done 是被后端禁止的 (4010), 若这里也不给
  // 编辑, 家长就彻底动不了这条记录了。
  const editBtn = el(`<button class="status-btn edit" aria-label="编辑这条作业" title="编辑">` +
    `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false">` +
    `<use href="#i-edit"></use></svg></button>`);
  editBtn.addEventListener('click', () => openEditAssignmentModal(item, refresh));
  actions.appendChild(editBtn);

  // 删除 —— 已完成的作业后端强制禁止删除 (4010), 所以这里直接不渲染,
  // 否则就是给用户一个"点了必然报错"的按钮。
  if (item.status !== 'done') {
    // 带 status-btn 让它跟随次操作档的小尺寸。删除是低频且不可逆的
    // 操作, 不该和"完成"抢视觉权重。
    const delBtn = el('<button class="status-btn danger">删除</button>');
    delBtn.addEventListener('click', async () => {
      // 弹窗里把科目和任务名都摆出来, 而不是只丢一句"确定删除 X?" ——
      // 作业名常有重复(比如多张卷子同名), 不看清科目就分不清删的是哪条。
      const ok = await confirmDialog({
        title: '删除这条作业?',
        lead: '这条作业会从看板和统计里一并移除, 且无法恢复。',
        target: item.task_name,
        subject: item.subject,
        note: item.actual_minutes != null ? `已记录的 ${item.actual_minutes} 分钟实际用时也会一起消失。` : '',
        okText: '删除',
        danger: true,
      });
      if (!ok) return;
      delBtn.disabled = true;                      // 请求锁: 防连点产生重复 DELETE
      try {
        await api('DELETE', `/assignments/${item.id}`);
        toast('已删除');
        refresh();
      } catch (e) {
        delBtn.disabled = false;
        toast('删除失败: ' + e.message);
      }
    });
    actions.appendChild(delBtn);
  }

  // 原文 popover
  const srcLink = row.querySelector('[data-action="show-source"]');
  if (srcLink && item.source_span) {
    srcLink.addEventListener('click', () => {
      openSourceModal(item);
    });
  }

  return row;
}

// ====== 编辑作业弹窗 ======
// 与「手动添加」共用 #modal 和 .form-grid, 字段口径保持一致 —— 家长在
// 两处看到的是同一套字段, 不用记"哪个弹窗能改哪个"。
//
// 语义是"改这一条", 所以表单要带出当前值而不是留空: 家长是来纠正
// AI 解析结果的(描述写错了、估时猜错了), 不是重填一遍。
function openEditAssignmentModal(item, onSaved) {
  if (_modalCloser) return;                  // 已有弹窗在等确认, 别覆盖它
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = '编辑作业';
  // deadline_time 存的是带秒的 ISO, 而 flatpickr 的 datetime 控件要的是
  // YYYY-MM-DDTHH:mm。截到 16 位正好去掉 ":ss", 直接塞进去会显示空白。
  // 秒本来就是补齐用的(见 openManualAddModal 的 dl + ':00'), 丢掉无损。
  const dlVal = item.deadline_time ? String(item.deadline_time).slice(0, 16) : '';
  // 培训/习惯任务要选项目, 复用快速添加已缓存的项目列表。
  if (!_qaProjects.length) refreshQuickAddData();
  const edKind = item.task_kind || 'subject';
  const isSubject = edKind === 'subject';
  document.getElementById('modal-content').innerHTML = `
    <div class="form-grid">
      <label>
        <span class="form-label">归属日</span>
        <input id="ed-due" type="date" class="js-datepicker" value="${escapeAttr(item.due_date)}" />
      </label>
      <label>
        <span class="form-label">任务来源</span>
        <select id="ed-kind">
          ${[['subject', '学科作业'], ['training', '培训'], ['habit', '习惯']]
            .map(([k, label]) => `<option value="${k}" ${k === edKind ? 'selected' : ''}>${label}</option>`).join('')}
        </select>
      </label>
      <label id="ed-subject-wrap" ${isSubject ? '' : 'hidden'}>
        <span class="form-label">科目</span>
        <select id="ed-subject">
          ${['语文', '数学', '英语']
            .map(s => `<option ${s === item.subject ? 'selected' : ''}>${s}</option>`).join('')}
        </select>
      </label>
      <label id="ed-project-wrap" ${isSubject ? 'hidden' : ''}>
        <span class="form-label">项目</span>
        ${projectPickerHtml('ed-project', item.project_id, item.project_name)}
      </label>
      <label>
        <span class="form-label">作业项</span>
        <input id="ed-name" maxlength="200" value="${escapeAttr(item.task_name)}" />
      </label>
      <label>
        <span class="form-label">作业类型</span>
        <span class="form-field">
          ${taskTypeComboHtml('ed-type', item.task_type || '', '背诵 / 订正 / 计算…')}
          <span class="form-hint">周复盘按类型分组, 可留空</span>
        </span>
      </label>
      <label>
        <span class="form-label">预计用时</span>
        <input id="ed-est" type="number" min="1" max="180" inputmode="numeric"
               value="${item.est_minutes != null ? item.est_minutes : ''}" />
      </label>
      <label>
        <span class="form-label">是否选做</span>
        <span class="form-field">
          <span class="form-check">
            <input id="ed-opt" type="checkbox" ${item.is_optional ? 'checked' : ''} /> <span>选做</span>
          </span>
          <span class="form-hint">不勾选即必做, 计入今日进度</span>
        </span>
      </label>
      <label>
        <span class="form-label">截止时间</span>
        <span class="form-field">
          <input id="ed-dl" type="datetime-local" class="js-datetimepicker" value="${dlVal}" />
          <span class="form-hint">清空即取消截止时间</span>
        </span>
      </label>
    </div>
  `;
  m.classList.remove('hidden');
  // 刻意不加 confirm-modal: 那是确认/单输入框的窄版布局(见 styles.css §16),
  // 整幅表单需要全宽, 与「手动添加」保持一致。
  const edKindSel = document.getElementById('ed-kind');
  edKindSel.addEventListener('change', () => {
    const toSubject = edKindSel.value === 'subject';
    document.getElementById('ed-subject-wrap').hidden = !toSubject;
    document.getElementById('ed-project-wrap').hidden = toSubject;
  });
  initDatePickersIn(document.getElementById('modal-content'));
  initCombos(document.getElementById('modal-content'));
  // 项目下拉: 自绘控件。当前项目可能已归档 (不在 GET /projects 的默认列表里),
  // 兜底项由 projectOptionList 补上, 否则下拉会默认选中第一个项目, 保存时
  // 把这条任务悄悄挪到别的项目上。
  initProjectPickers(document.getElementById('modal-content'));
  setModalActions({
    mode: 'confirm',
    okText: '保存',
    onOk: async () => {
      const name = document.getElementById('ed-name').value.trim();
      if (!name) { toast('作业项不能为空'); return; }   // 留在弹窗里, 别静默关掉
      const est = parseInt(document.getElementById('ed-est').value, 10);
      if (!(est >= 1 && est <= 180)) { toast('预计用时需在 1~180 分钟'); return; }
      const due = document.getElementById('ed-due').value;
      if (!due) {
        toast('归属日必填');
        openDatePicker(document.getElementById('ed-due'));
        return;
      }
      const dl = document.getElementById('ed-dl').value;
      // 归属日与截止时间的先后关系在前端也拦一次: 与后端 insert/edit 同一口径,
      // 但这里能立刻定位到是哪个框填错了, 不必等服务端报错再猜。
      if (dl && dl.slice(0, 10) < due) {
        toast('截止时间不能早于归属日');
        return;
      }
      const okBtn = document.getElementById('modal-ok');
      okBtn.disabled = true;                            // 请求锁: 防连点提交两次
      try {
        // 只发送"确实和原值不同"的字段。PATCH 接口靠 model_fields_set 判定
        // 哪些列要写, 全部回传会让清空操作(deadline_time) 表达不出来。
        const patch = {};
        const setIfChanged = (k, v) => { if (v !== item[k]) patch[k] = v; };
        setIfChanged('task_name', name);
        setIfChanged('est_minutes', est);
        // 任务来源联动: 学科作业带科目、清空项目; 培训/习惯反之。
        // subject 与 project_id 必须成对发送, 否则后端会看到
        // "training 却挂着科目"这种自相矛盾的组合 (4015)。
        // 比较基准取"库里的有效值": 老数据可能没有 task_kind (视为
        // subject)、project_id 可能是 undefined —— 直接和 null 比会让
        // 一次原样保存也发出 PATCH (后端记为 edited)。
        const kind = document.getElementById('ed-kind').value;
        const subjectKind = kind === 'subject';
        const pidRaw = document.getElementById('ed-project').value;
        const newSubject = subjectKind ? document.getElementById('ed-subject').value : null;
        const newProject = subjectKind || !pidRaw ? null : parseInt(pidRaw, 10);
        if (kind !== (item.task_kind || 'subject')) patch.task_kind = kind;
        if (newSubject !== (item.subject ?? null)) patch.subject = newSubject;
        if (newProject !== (item.project_id ?? null)) patch.project_id = newProject;
        setIfChanged('due_date', due);
        // 截到分钟再补秒, 与手动添加的写法对齐(见 openManualAddModal)
        setIfChanged('deadline_time', dl ? `${dl}:00` : null);
        setIfChanged('task_type', document.getElementById('ed-type').value.trim() || null);
        // 选做也能在这里改。setIfChanged 依赖两边同类型: 接口返回的
        // is_optional 是真布尔(models.py 里 bool(row[...]) 转换过), 而
        // .checked 也是布尔, 所以 !== 比较成立; 若哪天 DTO 改成 0/1,
        // 这里会恒为"有改动", 退化成每次保存都写 —— 不会丢数据, 但
        // 「没有改动」的提示会失效。
        setIfChanged('is_optional', document.getElementById('ed-opt').checked);

        if (Object.keys(patch).length === 0) {
          closeModal();
          toast('没有改动');
          return;
        }
        await api('PATCH', `/assignments/${item.id}`, patch);
        closeModal();
        // 改 due_date 会让这条作业从当前看的这一天里搬走, 必须重载整页,
        // 否则行还留在原处, 看起来像没生效。
        toast('已保存');
        if (onSaved) await onSaved();
      } catch (e) {
        okBtn.disabled = false;
        toast('保存失败: ' + e.message);               // 留在弹窗里让家长改完再试
      }
    },
  });
}

// 原文 modal (设计文档 §9 坑 3: 低置信度行可点开原文核对)
function openSourceModal(item) {
  const m = document.getElementById('modal');
  m.classList.remove('hidden');
  document.getElementById('modal-title').textContent = `原文 · ${item.subject} · ${item.task_name}`;
  const conf = item.confidence;
  document.getElementById('modal-content').innerHTML = `
    <p class="muted">置信度: ${confidencePill(conf)}</p>
    <blockquote style="background:var(--surface-2); padding:var(--space-3); border-radius:var(--radius-md); margin:var(--space-2) 0; font-size:var(--fs-body-s);">${escapeAttr(item.source_span)}</blockquote>
    ${item.deadline_time ? `<p>⏰ 截止: ${fmtDateTime(item.deadline_time)}</p>` : ''}
    ${item.est_minutes != null ? `<p>预计用时: ${item.est_minutes} 分钟</p>` : ''}
    ${item.actual_minutes != null ? `<p>实际用时: ${item.actual_minutes} 分钟</p>` : ''}
  `;
  // 原文弹窗是只读的, 取消与确定都只是关闭, 并排两个同功能按钮
  // 会让家长以为"确定"另有含义。这里只留一个「返回」。
  setModalActions({ mode: 'close' });
}

// 弹窗底部那颗次要键的实际动作。默认关窗; 嵌套弹窗 (从「固定课表」进到
// 取消键 / Esc / 点遮罩三条路径都走这里 —— 按键上写着「返回」, 按 Esc 却把
// 整个弹窗关掉, 一样会让人以为按错了。

  fn();
}

// 弹窗底部按钮按语义切换 (2026-09-28)。
//   close  —— 只读弹窗: 隐藏取消, 「确定」改文案为「返回」
//   confirm —— 输入弹窗: 保留「取消」+「确定」, 由调用方接确定按钮的 onclick
// 不能简单地删掉取消按钮: 手动添加弹窗里"取消"(放弃)与"确定"(提交)
// 是两种不同操作, 合并会丢掉"放弃这次编辑"的语义。
// (回到上一层的列表), 不是"取消"(放弃这次编辑) —— 上一层的列表还在, 按一下
// 回去比把整个弹窗关掉更顺手。
// okText / danger 是 2026-09-29 为确认弹窗加的: 危险操作的确认键要写
// 「删除」而不是「确定」, 且要用实心红 —— 让家长在按下之前再看一眼
// 自己要做什么, 而不是靠"确定"两个字去猜。
function setModalActions({ mode, onOk = null, okText = null, danger = false }) {
  const cancel = document.getElementById('modal-cancel');
  const ok = document.getElementById('modal-ok');
  // 每次开弹窗都要把确定键恢复可点。
  // modal-ok 是 index.html 里那个常驻节点, 不会随弹窗内容一起重建, 而
  // 提交类弹窗会把它 disabled 当"请求锁"防连点 —— 那个锁若不在这里收回,
  // 一次成功提交之后确定键就永久变灰: 家长第二次点「编辑」/「删除」/「改用时」
  // 会看到弹窗正常打开、但确定键点不动, 表现为"功能坏了"。
  // 收回放在这个统一出口, 而不是各弹窗自己记着, 才不会漏。
  ok.disabled = false;
  // 次要键的行为同样要重设: 弹窗是共用节点, 上一个弹窗留下的"返回上一层"
  // 不能带到下一个弹窗去。
  if (mode === 'close') {
    // 只读弹窗: 「取消」与「返回」做的是同一件事 (都只是关窗), 摆两颗
    cancel.classList.add('hidden');
    ok.textContent = '返回';
    ok.classList.remove('primary');
    ok.classList.add('ghost', 'modal-solo-btn');
    ok.classList.remove('modal-danger');
    ok.onclick = () => closeModal();
  } else {
    cancel.classList.remove('hidden');
    ok.textContent = '确定';
    ok.classList.add('primary');
    ok.classList.remove('ghost', 'modal-solo-btn');
    ok.classList.toggle('modal-danger', danger);
    if (okText) ok.textContent = okText;
    ok.onclick = onOk;
  }
}

// ====== 弹窗关闭的统一出口 (2026-09-29) ======
// 弹窗一共有四条关闭路径: 取消按钮 / 确定按钮 / Esc / 点遮罩。此前每处
// 都自己写 classList.add('hidden'), 彼此不通 —— 改成 Promise 化的
// confirmDialog / promptNumber 之后, 关闭必须能把结果 notify 回调用方,
// 否则 `await confirmDialog(...)` 会永远挂起, 点取消等于卡死。
// 收口到这一个函数后, 无论从哪条路关闭, Promise 都有结果且只结算一次。

// "被取消"的哨兵。closeModal 只知道弹窗关了, 并不知道该给调用方回什么
// 类型: 确认弹窗要 boolean, 数字输入弹窗要 null —— 而后者不能拿 falsy
// 当取消, 因为 0 是合法用时。用显式哨兵把"取消"和"取到值"分开, 由每个
// 弹窗自己翻译, 就不会出现"取消被当成 0 提交上去"这类静默错误。
const CANCELLED = Symbol('modal-cancelled');

let _modalCloser = null;

// 收起还开着的 flatpickr 日历浮层。
//
// flatpickr 把日历挂在 body 下而不是输入框所在的弹窗里, 所以
// closeModal 给 #modal 加 .hidden 收不住它: 浮层会带着 display:block
// 继续浮在页面上, 轻则遮住看板, 重则挡住"保存"按钮的点击命中
// (实测点保存会 hit test 到日历里的数字 span, 按钮点不动)。
// 手动添加和编辑作业两个弹窗都初始化了日期控件, 所以在这里统一收口。
//
// 实例从我们自己存的 _fpInstance 拿, 而不是从浮层元素上找:
// 实测浮层元素上没有任何 flatpickr 私有属性(库没挂), 猜
// el._flatpickr 只会永远走兜底分支, 跳过库自己的关闭流程。
//
// 也不能写 querySelectorAll('input._fpInstance'): 那是 CSS 属性选择器,
// 只匹配真实 HTML attribute, 而 _fpInstance 是 initDatePicker 挂的
// JS 属性, 选择器一个都选不到(实测返回空)。只能取全部 input 再逐个读。
function closeAnyOpenDatePicker() {
  const inputs = document.querySelectorAll('input, [type="hidden"]');
  let closed = 0;
  inputs.forEach(input => {
    const inst = input._fpInstance;
    if (!inst || typeof inst.close !== 'function') return;
    // 只关还开着的: close() 有 blur 等副作用, 对已关闭的浮层
    // 没有必要再走一遍。
    if (!inst.isOpen) return;
    inst.close();
    closed += 1;
  });
  // 兜底: 万一有浮层没对应的 input 记录(理论上不该发生),
  // 至少把 open 类摘掉, 别让它留在页面上挡住点击。
  document.querySelectorAll('.flatpickr-calendar.open').forEach(el => {
    el.classList.remove('open', 'animate');
  });
  return closed;
}

function closeModal(result = CANCELLED) {
  const m = document.getElementById('modal');
  if (m) {
    m.classList.add('hidden');
    // 窄版样式只属于确认/输入弹窗。在这里(而不是各开启处)收掉,
    // 就不必担心它残留到下一次打开的原文或手动添加弹窗上。
    m.classList.remove('confirm-modal');
  }
  // 必须在 #modal 隐藏之后: 浮层在 body 下, 不受 modal 的 hidden 影响。
  closeAnyOpenDatePicker();
  const fn = _modalCloser;
  _modalCloser = null;          // 先清空再回调: 回调里若又开了新弹窗, 不会被顶掉
  if (fn) fn(result);
}

// 通用确认弹窗 —— 替代浏览器原生 confirm()。
// 原生弹窗由浏览器绘制, 完全脱离页面样式: 在这套奶油色调里是一块突兀的
// 系统灰, 按钮顺序还随平台变, 且无法做任何防误触处理。放进自己的弹窗后
// 才做得到"红色实心删除键 + 默认焦点落在取消 + 回车不直接删"。
function confirmDialog({ title, lead, target = '', subject = '', note = '', okText = '确定', danger = false }) {
  // 已有弹窗在等确认时, 忽略重复点击: 否则第二次点击会覆盖 _modalCloser,
  // 第一个 Promise 再无人结算, 调用方的 await 就永久挂起了。
  if (_modalCloser) return Promise.resolve(false);
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-content').innerHTML = `
    <div class="confirm-body${danger ? ' danger' : ''}">
      <div class="confirm-icon" aria-hidden="true">⚠</div>
      <div class="confirm-text">
        <p class="confirm-lead">${escapeAttr(lead)}</p>
        ${target ? `<p class="confirm-target">${subject ? `<span class="subject-tag ${escapeAttr(subject)}">${escapeAttr(subject)}</span>` : ''}<span>${escapeAttr(target)}</span></p>` : ''}
        ${note ? `<p class="confirm-note">${escapeAttr(note)}</p>` : ''}
      </div>
    </div>`;
  m.classList.remove('hidden');
  m.classList.add('confirm-modal');
  return new Promise((resolve) => {
    // 只把"确认"翻译成 true, 其余(取消/Esc/遮罩)一律 false
    _modalCloser = (r) => resolve(r === true);
    setModalActions({ mode: 'confirm', okText, danger, onOk: () => closeModal(true) });
    // 焦点默认落在「取消」: 不可逆操作不该让家长随手一敲回车就删掉
    document.getElementById('modal-cancel').focus();
  });
}

// 通用数字输入弹窗 —— 替代原生 prompt()。
// 原生 prompt 的输入框是系统控件, 字体/圆角/间距全都不受页面控制, 在
// 触屏上更是割裂。这里用弹窗 + number 输入, 回车提交, 取消/Esc 返回 null。
function promptNumber({ title, label, value, min = 0, max = 240, hint = '', okText = '保存' }) {
  if (_modalCloser) return Promise.resolve(null);
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-content').innerHTML = `
    <div class="prompt-body">
      <label class="prompt-label" for="prompt-num">${escapeAttr(label)}</label>
      <input id="prompt-num" type="number" inputmode="numeric" min="${min}" max="${max}" value="${escapeAttr(value)}" />
      ${hint ? `<p class="prompt-hint">${escapeAttr(hint)}</p>` : ''}
    </div>`;
  m.classList.remove('hidden');
  m.classList.add('confirm-modal');
  return new Promise((resolve) => {
    // 取消翻译成 null 而不是 false/0 —— 调用方靠 === null 判"没填",
    // 若这里回 0, 家长点一下取消就会把用时改成 0 分钟。
    _modalCloser = (r) => resolve(r === CANCELLED ? null : r);
    const input = document.getElementById('prompt-num');
    const submit = () => {
      const n = Number(input.value);
      // 越界要留在弹窗里报错, 不能直接关掉: 悄悄关掉会让家长以为改完了,
      // 实际什么也没改, 还得再点一次「改用时」才发现。
      if (input.value.trim() === '' || !Number.isFinite(n) || n < min || n > max) {
        input.classList.add('error');
        toast(`请填 ${min}~${max} 之间的数字`);
        return;
      }
      closeModal(n);
    };
    setModalActions({ mode: 'confirm', okText, onOk: submit });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    input.focus();
    input.select();
  });
}

// 否则嵌套弹窗里按 Esc 会把整个弹窗关掉, 而按「返回」却只退一层。
document.getElementById('modal-cancel').addEventListener('click', () => {
  closeModal();
});

// Esc 与点遮罩关闭 —— 弹窗的标准行为, 此前这两条路都不通。
// Esc 只在弹窗打开时响应, 否则会误吞页面其他地方的 Esc。
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const m = document.getElementById('modal');
  if (m && !m.classList.contains('hidden')) closeModal();
});
// e.target === e.currentTarget 才算点到遮罩: 点击弹窗内容(冒泡上来)不能关
document.getElementById('modal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeModal();
});

// ====== 录入 / 解析 ======
document.getElementById('btn-parse').addEventListener('click', parseRaw);
document.getElementById('btn-add-manual').addEventListener('click', () => openManualAddModal());

// ====== 快速添加 (M1 统一任务池) ======
// 钢琴/篮球/阅读这类任务每天重复, 让家长每次粘贴通知纯属浪费。
// 这里把"项目 + 模板任务"做成一次点击; 后端复用批量入库的去重与项目
// 归属校验, 所以连点两下不会变成两条, 也不会把任务挂到别人家项目上。
let _qaKind = 'subject';
let _qaProjects = [];
let _qaTemplates = [];

async function refreshQuickAddData() {
  try {
    const [pr, tp] = await Promise.all([
      api('GET', '/projects'),
      api('GET', '/project-templates'),
    ]);
    _qaProjects = pr.items || [];
    _qaTemplates = tp.items || [];
  } catch (e) {
    // 辅助入口拉取失败不该阻断主流程 (粘贴解析 / 手动添加仍可用)
  }
  // 项目列表变了, 所有已挂载的项目选择器都要重画 (否则新建的项目在别处看不到)。
  refreshProjectPickers();
  renderQuickTemplates();
}

function renderQuickTemplates() {
  const box = document.getElementById('qa-templates');
  if (!box) return;
  box.innerHTML = '';
  _qaTemplates.forEach(t => {
    const b = el(`<button type="button" class="qa-tpl ${t.task_kind}" data-kind="${t.task_kind}">${escapeAttr(t.label)}</button>`);
    b.title = t.tasks.map(x => `${x.task_name} ${x.est_minutes}分`).join(' / ');
    b.addEventListener('click', () => applyTemplate(t));
    box.appendChild(b);
  });
}

function setQuickKind(kind) {
  _qaKind = ['subject', 'training', 'habit'].includes(kind) ? kind : 'subject';
  document.querySelectorAll('#qa-kinds .qa-kind').forEach(b =>
    b.classList.toggle('active', b.dataset.kind === _qaKind));
  const isSubject = _qaKind === 'subject';
  document.getElementById('qa-subject-row').hidden = !isSubject;
  document.getElementById('qa-project-row').hidden = isSubject;
}

async function quickAddTask(payload) {
  return api('POST', '/assignments/quick', Object.assign(
    { due_date: entryDate.value || todayStr(), est_minutes: 15 }, payload));
}

async function applyTemplate(t) {
  try {
    // 项目创建是幂等的: 同名同类型返回已有项目, 重复点模板不会建出一堆"钢琴"。
    const project = await api('POST', '/projects', {
      name: t.default_name, project_type: t.project_type,
    });
    let added = 0, skipped = 0;
    for (const task of t.tasks) {
      const r = await quickAddTask({
        task_name: task.task_name,
        task_kind: t.task_kind,
        project_id: project.id,
        est_minutes: task.est_minutes,
        task_type: task.task_type || null,
      });
      if (r.skipped) skipped += 1; else added += 1;
    }
    await refreshQuickAddData();
    toast(skipped
      ? `「${t.label}」已添加 ${added} 项, ${skipped} 项今天已有`
      : `「${t.label}」已添加 ${added} 项`);
  } catch (e) { toast('添加失败: ' + e.message); }
}

async function quickAddFromForm() {
  const name = document.getElementById('qa-name').value.trim();
  if (!name) { toast('先填任务名'); return; }
  const est = parseInt(document.getElementById('qa-est').value, 10);
  if (!(est >= 1 && est <= 180)) { toast('预计用时需在 1~180 分钟'); return; }
  const payload = { task_name: name, est_minutes: est };
  if (_qaKind === 'subject') {
    payload.task_kind = 'subject';
    payload.subject = document.getElementById('qa-subject').value;
  } else {
    payload.task_kind = _qaKind;
    const pid = parseInt(document.getElementById('qa-project').value, 10);
    payload.project_id = Number.isFinite(pid) ? pid : null;
  }
  try {
    const r = await quickAddTask(payload);
    if (r.skipped) {
      toast(`「${name}」今天已经加过了`);
    } else {
      toast(`已添加「${name}」`);
      document.getElementById('qa-name').value = '';
    }
  } catch (e) { toast('添加失败: ' + e.message); }
}

function initQuickAdd() {
  document.querySelectorAll('#qa-kinds .qa-kind').forEach(b => {
    b.addEventListener('click', () => setQuickKind(b.dataset.kind));
  });
  document.getElementById('qa-add').addEventListener('click', quickAddFromForm);
  document.getElementById('qa-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') quickAddFromForm();
  });
  // 培训/习惯任务的项目选择: 与各弹窗共用同一个自绘下拉 (含「＋新建项目」)。
  // 候选随项目列表异步到达, 所以先挂好控件, 列表由 refreshProjectPickers 补上。
  const host = document.getElementById('qa-project-row');
  if (host) {
    host.innerHTML = projectPickerHtml('qa-project');
    initProjectPickers(host);
  }
  setQuickKind('subject');
  refreshQuickAddData();
}
initQuickAdd();

// 字符计数 (设计文档 §7.2 第 1 块)
const rawTextarea = document.getElementById('raw-text');
function syncCharCount() {
  const len = rawTextarea.value.length;
  const num = document.getElementById('char-count-num');
  num.textContent = len;
  num.parentElement.classList.toggle('warn', len > MAX_TEXT_LEN * 0.9);
}
rawTextarea.addEventListener('input', syncCharCount);

// 「填入示例 / 清空」: 示例原先只写在 placeholder 里, 一开始输入就没了,
// 家长想照格式写时已经看不到了。改成随时能重新调出示例。
const SAMPLE_RAW = '【9.21英语】\n1. 一起作业; 2. 描写本 P11-12; 3. 背课文 P18-19。';
document.getElementById('btn-fill-sample').addEventListener('click', () => {
  rawTextarea.value = SAMPLE_RAW;
  syncCharCount();
  rawTextarea.focus();
});
document.getElementById('btn-clear-raw').addEventListener('click', () => {
  rawTextarea.value = '';
  syncCharCount();
  rawTextarea.focus();
});

async function parseRaw() {
  const raw = rawTextarea.value;
  const status = document.getElementById('parse-status');
  const result = document.getElementById('parse-result');
  if (!raw.trim()) { status.textContent = '请粘贴内容'; return; }

  // 立即显示 spinner 加载态 (后端 timeout=120s + 1 次 +2s 重试 = 最坏 242s)
  status.innerHTML = '<span class="spinner" aria-hidden="true"></span>AI 解析中, 最长可能需 2 分钟';
  result.innerHTML = '<p class="muted">预计 15~120 秒, 请稍候 (服务端内部会自动重试 1 次)</p>';

  // 130s 客户端超时 (略长于服务端单次 120s, 比服务端总耗时短, 快速降级避免白屏)
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 130000);

  try {
    const httpResp = await fetch(API + '/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw_text: raw }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    const text = await httpResp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }

    if (!httpResp.ok) {
      // 5xx 等真异常 (理论上不该出现, parse 路由已包成 200)
      const err = (data && data.error) || { code: '???', message: text || httpResp.statusText };
      throw new Error(`[${err.code}] ${err.message}`);
    }

    // ====== 结构化错误 (HTTP 200 + ok=false) ======
    if (data && data.ok === false && data.error) {
      handleParseError(data.error, raw);
      status.textContent = '解析失败';
      return;
    }

    // ====== 成功路径 ======
    const items = (data && data.items) || [];
    status.textContent = `解析完成, ${items.length} 条`;
    if (!items.length) {
      result.innerHTML = '';
      result.appendChild(el('<div class="muted">未识别到作业, 可手动添加</div>'));
      return;
    }
    // 渲染前先把设置页的基线拉下来 —— 预览表里 AI 没给时长的行要用它兜底,
    // 顺序反了会让这一批行先渲染成旧的魔法数 15.
    await loadBaselines();
    renderPreviewTable(items, data.batch_id);
  } catch (e) {
    clearTimeout(timeoutId);
    if (e && e.name === 'AbortError') {
      // 客户端超时 (服务端超时 120s + 1 次重试 = 最坏 242s, 但 130s 时前端先放弃避免白屏)
      handleParseTimeout(raw);
      return;
    }
    status.textContent = '失败';
    result.innerHTML = `<div class="error-banner">解析失败: ${e.message}</div>`;
  }
}

// 解析失败 (后端返结构化错误, HTTP 200)
function handleParseError(errorObj, raw) {
  const result = document.getElementById('parse-result');
  result.innerHTML = '';
  const code = (errorObj && errorObj.code) || 'AI_UNKNOWN';
  const msg = (errorObj && errorObj.message) || '未知错误';
  let bannerText = '';
  if (code === 'AI_KEY_MISSING') {
    bannerText = 'AI Key 未配置, 仅手动添加可用';
    result.appendChild(el(`<div class="error-banner">${escapeAttr(bannerText)}</div>`));
    return;  // 无重试意义 (Key 不在, 重试还是同样的错)
  } else if (code === 'AI_TIMEOUT') {
    bannerText = `解析超时已自动重试 1 次 (${msg})。可手动录入或稍后重试`;
  } else if (code === 'AI_NETWORK') {
    bannerText = `网络异常 (${msg})。可手动录入或稍后重试`;
  } else if (code === 'AI_FORMAT') {
    bannerText = `AI 返回格式异常 (${msg})。可手动录入或稍后重试`;
  } else {
    bannerText = `AI 解析失败 (${code}): ${msg}`;
  }
  result.appendChild(el(`<div class="error-banner">${escapeAttr(bannerText)}</div>`));
  appendRetryCopyRow(raw);
}

// 客户端超时 (130s 主动 abort)
function handleParseTimeout(raw) {
  const status = document.getElementById('parse-status');
  const result = document.getElementById('parse-result');
  status.textContent = '超时';
  result.innerHTML = '';
  result.appendChild(el('<div class="error-banner">解析超时已自动重试 1 次 (后端限制 120s)。可手动录入或稍后重试</div>'));
  appendRetryCopyRow(raw);
}

// 重试 + 复制原文按钮行
function appendRetryCopyRow(raw) {
  const result = document.getElementById('parse-result');
  const row = el('<div class="row"></div>');
  const retryBtn = el('<button class="primary">重试</button>');
  const copyBtn = el('<button class="ghost">复制原文</button>');
  retryBtn.addEventListener('click', parseRaw);
  copyBtn.addEventListener('click', () => copyToClipboard(raw));
  row.append(retryBtn, copyBtn);
  result.appendChild(row);
}

// 复制原文到剪贴板 (兼容老浏览器)
function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toast('已复制原文'),
      () => fallbackCopy(text)
    );
    return;
  }
  fallbackCopy(text);
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  document.body.removeChild(ta);
  toast(ok ? '已复制原文' : '复制失败, 请手动选中');
}

// AI 没给预估时长时的兜底: 优先用家长在设置页配的基线, 而不是写死的 15.
// 后端 _resolve_est_minutes 已经按 "任务类型基线 > AI 估值 > 科目基线" 填过一次,
// 这层只在连科目基线都没有时兜底, 但它必须读真实配置 ——
// 写死 15 会让家长在设置页调完数字后, 仍然看到与配置无关的时长.
let _baselineCache = null;

function setBaselineCache(obj) { _baselineCache = obj; }

function estDefault(item) {
  const b = _baselineCache || {};
  const byType = b.task_type || {};
  const bySubject = b.subject || {};
  return byType[item && item.task_type]
    || bySubject[item && item.subject]
    || 15;  // 配置全缺时的最后兜底 (与后端 DEFAULT_TASK_TYPE_BASELINE 同量级)
}

// 作业类型候选 (2026-10-02)。
//
// 候选不再写死一份, 而是直接取设置页「各作业类型预估用时」里的键 ——
// 家长在设置里加过自定义类型(如「听写」), 这里的候选必须跟着变, 否则
// 新增/编辑弹窗里选不到那个类型, 也就永远命中不了它的预估值。
// 写死 ['订正','背诵',...] 的话, 配置孤岛问题会以"选得到但没预填"
// 的形式复发一次 —— 那正是 2026-09-29 报障的同一个坑。
function taskTypeOptions() {
  const byType = (_baselineCache || {}).task_type || {};
  const keys = Object.keys(byType);
  // 配置没拉到(loadBaselines 还没回来/接口挂了)时给一份常见值兜底,
  // 宁可让家长有候选可选, 也不要一个空 datalist。
  return keys.length ? keys : ['订正', '背诵', '预习', '阅读', '计算', '抄写'];
}

// 作业类型下拉的 HTML 骨架 (自绘, 不用 <datalist>)。
//
// 为什么不用 datalist: 它的候选面板由操作系统绘制, **无法用 CSS 改任何一处**
// —— 底色、文字、圆角、高亮都不是我们的。项目里已经因为同样的原因把原生
// prompt() 和 <input type="date"> 换成了自绘控件 (见 promptNumber 与 flatpickr
// 段落)。datalist 是同一类问题: 在 Windows Chrome 上它是一块近黑底浅色字的
// 系统面板, 落在奶油糖果配色里像贴错了别的应用的截图。
//
// 结构: 一个真 input (仍可自由输入, 不只是选) + 一个 .combo-menu 浮层。
// 保留自由输入很重要: 家长可能想写一个设置里还没有的类型。
function taskTypeComboHtml(id, value, placeholder) {
  return `
    <span class="combo" data-combo>
      <input id="${id}" class="combo-input" maxlength="50" autocomplete="off"
             placeholder="${escapeAttr(placeholder || '')}"
             value="${escapeAttr(value || '')}"
             role="combobox" aria-expanded="false" aria-autocomplete="list" aria-haspopup="listbox" />
      <button type="button" class="combo-toggle" tabindex="-1" aria-label="展开作业类型候选"></button>
      <ul class="combo-menu" role="listbox" hidden></ul>
    </span>`;
}

// 绑定下拉交互。抽成函数而不是内联 onclick, 是为了编辑/新增两处复用同一套
// 键盘与关闭逻辑 —— 复制两份必然分叉。
//
// @param root  .combo 容器
// @param onPick 选中候选后的回调(用来触发预计用时重新预填)
function bindCombo(root, onPick) {
  if (!root) return;
  const input = root.querySelector('.combo-input');
  const toggle = root.querySelector('.combo-toggle');
  const menu = root.querySelector('.combo-menu');
  if (!input || !menu) return;

  let options = taskTypeOptions();   // 当前候选(每次展开时从设置重取)
  let active = -1;                   // 键盘高亮项下标, -1 = 无
  let suppressOpen = false;          // 选中候选后短暂阻止 focus 重新展开

  const close = () => {
    menu.hidden = true;
    root.classList.remove('is-open');
    input.setAttribute('aria-expanded', 'false');
    active = -1;
  };

  const render = () => {
    const q = input.value.trim();
    // 输入框已有内容时用它过滤: 家长打到"背"只应看到"背诵"。
    // 全部匹配则显示全部候选, 免得"输了字反而候选变少"的反直觉。
    const list = q
      ? options.filter(o => o.includes(q))
      : options;
    menu.innerHTML = list.length
      ? list.map((o, i) => `<li class="combo-option" role="option" data-value="${escapeAttr(o)}"
             aria-selected="false">${escapeAttr(o)}</li>`).join('')
      : `<li class="combo-empty" role="presentation">没有匹配的候选, 可直接输入</li>`;
    active = -1;
    menu.hidden = false;
    root.classList.add('is-open');
    input.setAttribute('aria-expanded', 'true');
  };

  const open = () => { options = taskTypeOptions(); render(); };

  const pick = (value) => {
    // 先关闸门再干活: onPick 会派发 input 事件、input.focus() 又会触发
    // focus 监听, 两者都会调 open() 把浮层弹回来。顺序上必须让
    // suppressOpen 先就位, 否则"选中后浮层立刻重新展开", 看起来像
    // 点不中选项。
    suppressOpen = true;
    input.value = value;
    close();
    // 手动赋值不会触发 input 事件, 预填链路要显式通知一次。
    if (onPick) onPick(value);
    input.focus();
    suppressOpen = false;
  };

  const highlight = (next) => {
    const items = [...menu.querySelectorAll('.combo-option')];
    if (!items.length) return;
    active = (next + items.length) % items.length;
    items.forEach((el, i) => {
      el.classList.toggle('is-active', i === active);
      el.setAttribute('aria-selected', i === active ? 'true' : 'false');
    });
    // scrollIntoView 在真实浏览器里滚动浮层, 但 jsdom 没实现它, 调了会抛
    // TypeError 并中断整个 keydown 处理 —— 键盘导航会随之失灵。防御性跳过。
    if (typeof items[active].scrollIntoView === 'function') {
      items[active].scrollIntoView({ block: 'nearest' });
    }
  };

  // 点外面关闭。用 capture 阶段的 document 监听, 因为选项自身的 click
  // 处理器也在冒泡阶段, 若在冒泡里判断 contains 会因为顺序问题误关。
  document.addEventListener('pointerdown', (e) => {
    if (!root.contains(e.target)) close();
  }, true);

  toggle.addEventListener('click', () => {
    menu.hidden ? open() : close();
  });
  input.addEventListener('focus', () => { if (!suppressOpen) open(); });
  // input 也要看闸门: pick() 内部会派发 input 通知预填, 那时不该顺带
  // 把浮层重新撑开(否则"选完就又弹回来", 家长要点两下才能收起)。
  input.addEventListener('input', () => { if (!suppressOpen) open(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { close(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); menu.hidden ? open() : highlight(active + 1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); highlight(active - 1); return; }
    if (e.key === 'Enter') {
      // 展开且有高亮项时, Enter 是"选中"; 否则让表单正常提交。
      if (!menu.hidden && active >= 0) {
        e.preventDefault();
        const el = menu.querySelectorAll('.combo-option')[active];
        if (el) pick(el.dataset.value);
      }
      return;
    }
    if (e.key === 'Tab') close();   // 切走时收起, 别把浮层留在别的框下面
  });
  menu.addEventListener('click', (e) => {
    const li = e.target.closest('.combo-option');
    if (li) pick(li.dataset.value);
  });
  menu.addEventListener('pointerover', (e) => {
    const li = e.target.closest('.combo-option');
    if (!li) return;
    const items = [...menu.querySelectorAll('.combo-option')];
    active = items.indexOf(li);
    items.forEach((el, i) => el.classList.toggle('is-active', i === active));
  });
}

// 一次性把弹窗里的作业类型下拉都接上。必须在 innerHTML 写入之后调用。
// onPick 统一走 bindEstAutofill 里的重算, 保证选中候选后预计用时立刻更新。
function initCombos(scope) {
  (scope || document).querySelectorAll('[data-combo]').forEach((root) => {
    const input = root.querySelector('.combo-input');
    bindCombo(root, () => {
      // 复用 input 事件路径: 派发一个 input 让 autofill 的监听器收到。
      // 直接调 apply() 会与 autofocus 的实现耦合, 这里保持单一入口。
      if (input) input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });
}

// ====== 项目选择器 (自绘下拉 + 就地新建 · 2026-10-30) ======
//
// 为什么不用原生 <select>: 展开后的候选面板由操作系统绘制, CSS 一处也
// 改不动 —— Windows Chrome 上是蓝底白字的系统面板, 落在奶油糖果配色里
// 像贴错了别的应用。与本文件替换原生 prompt() / <input type="date"> /
// datalist 同一类原因, 所以项目下拉也自绘。
//
// 结构与 .combo 一致: 一个触发键 + 一个候选浮层, 只是把"可自由输入"
// 换成"从已有项目里选"。选中值放在一个 hidden input 里 —— id 与原来的
// <select> 相同, 于是各弹窗里 getElementById('xx-project').value 的
// 读取代码一行都不用改。
//
// 触发键右边常驻一个「＋新建项目」: 项目一个都没有时, 家长原本只能跳去
// 设置页或录入页绕一圈, 弹窗里等于死路 (2026-10-30 用户反馈)。
function projectOptionList(selectedId, selectedName) {
  const list = (_qaProjects || []).map(p => ({ value: String(p.id), label: p.name }));
  const sid = selectedId == null ? '' : String(selectedId);
  if (sid && !list.some(o => o.value === sid)) {
    list.unshift({ value: sid, label: selectedName || '当前项目' });
  }
  return list;
}

// 项目选择器的 HTML。selectedId 为空时默认落在第一个项目上 —— 原生
// <select> 也是这个默认 (第一个 option 自动选中), 保持一致: 家长只改了
// 时间, 不该因为"没碰项目"而被拦下。
function projectPickerHtml(id, selectedId, selectedName) {
  const opts = projectOptionList(selectedId, selectedName);
  const sid = selectedId == null ? '' : String(selectedId);
  let value = sid;
  let label = '';
  if (value) {
    const hit = opts.find(o => o.value === value);
    label = hit ? hit.label : (selectedName || '当前项目');
  } else if (opts.length) {
    value = opts[0].value;
    label = opts[0].label;
  }
  const empty = !value;
  return `<span class="picker" data-picker data-selected="${escapeAttr(value)}" data-name="${escapeAttr(label)}">
    <input type="hidden" id="${id}" value="${escapeAttr(value)}" />
    <span class="picker-row">
      <span class="picker-slot">
        <button type="button" class="picker-trigger" aria-haspopup="listbox" aria-expanded="false">
          <span class="picker-value${empty ? ' is-empty' : ''}">${escapeAttr(empty ? '还没有项目' : label)}</span>
          <span class="picker-caret" aria-hidden="true"></span>
        </button>
        <span class="picker-panel" hidden>
          <span class="picker-list" role="listbox" aria-label="项目"></span>
          <span class="picker-create" hidden>
            <input class="picker-create-input" type="text" maxlength="40" placeholder="新项目名, 如 钢琴" aria-label="新项目名" />
            <button type="button" class="secondary picker-create-ok">建好</button>
          </span>
        </span>
      </span>
      <button type="button" class="picker-new">＋新建项目</button>
    </span>
  </span>`;
}

// 绑定一个项目选择器。调用前 innerHTML 必须已经写好。
function bindProjectPicker(root) {
  if (!root || root.dataset.bound === '1') return;
  root.dataset.bound = '1';
  const hidden = root.querySelector('input[type="hidden"]');
  const trigger = root.querySelector('.picker-trigger');
  const valueEl = root.querySelector('.picker-value');
  const panel = root.querySelector('.picker-panel');
  const listEl = root.querySelector('.picker-list');
  const newBtn = root.querySelector('.picker-new');
  const createBox = root.querySelector('.picker-create');
  const createInput = root.querySelector('.picker-create-input');
  const createOk = root.querySelector('.picker-create-ok');
  if (!hidden || !trigger || !valueEl || !panel || !listEl) return;

  const origId = root.dataset.selected || '';
  const origName = root.dataset.name || '';
  let current = hidden.value;

  // 候选 = 现存项目 + (当前项目已归档时的兜底项)。兜底项只在"当前值
  // 仍是那个已归档项目"时才有意义, 换成别的项目后它就该消失。
  const options = () => projectOptionList(current, current === origId ? origName : '');

  const renderList = () => {
    const opts = options();
    listEl.innerHTML = opts.length
      ? opts.map(o => `<button type="button" class="picker-option${o.value === current ? ' is-selected' : ''}" role="option" aria-selected="${o.value === current}" data-value="${escapeAttr(o.value)}">${escapeAttr(o.label)}</button>`).join('')
      : '<span class="picker-empty">还没有项目 —— 在下面建一个</span>';
  };

  const renderLabel = () => {
    if (current) {
      const o = options().find(x => x.value === current);
      valueEl.textContent = o ? o.label : (origName || '当前项目');
      valueEl.classList.remove('is-empty');
    } else {
      valueEl.textContent = '还没有项目';
      valueEl.classList.add('is-empty');
    }
  };

  // 值与候选的同步: 项目列表变了 (新建/归档/首次加载) 后重新对一遍。
  // 与原生 <select> 一致: 只要还有候选, 就一定有一个选中项 —— 少了这条,
  // 家长明明看到下拉里有项目, 提交时却被当成"没选项目"。
  const sync = () => {
    const opts = options();
    if (!current && opts.length) {
      current = opts[0].value;
      hidden.value = current;
    }
    renderLabel();
    renderList();
  };

  const close = () => {
    panel.hidden = true;
    root.classList.remove('is-open');
    trigger.setAttribute('aria-expanded', 'false');
    showList();
  };
  const open = () => {
    renderList();
    panel.hidden = false;
    root.classList.add('is-open');
    trigger.setAttribute('aria-expanded', 'true');
    // 一个项目都没有时直接进新建态 —— 让家长先看一个空列表再点"新建",
    // 是白白多一次点击。
    if (!listEl.querySelector('.picker-option')) startCreate();
  };
  // 新建态: 浮层里换成输入框, 候选列表让位。
  const showList = () => {
    listEl.hidden = false;
    createBox.hidden = true;
  };
  const startCreate = () => {
    listEl.hidden = true;
    createBox.hidden = false;
    createInput.value = '';
    createInput.focus();
  };
  const pick = (value, label) => {
    current = value;
    hidden.value = value;
    valueEl.textContent = label;
    valueEl.classList.remove('is-empty');
    close();
    trigger.focus();
  };

  const create = async () => {
    const name = createInput.value.trim();
    if (!name) { toast('先填项目名'); return; }
    createOk.disabled = true;
    try {
      const p = await api('POST', '/projects', { name });
      current = String(p.id);
      hidden.value = current;
      valueEl.textContent = p.name;
      valueEl.classList.remove('is-empty');
      createInput.value = '';
      // 刷新项目列表: 新项目要出现在候选里, 别的选择器也要跟着更新。
      await refreshQuickAddData();
      renderList();
      close();
      toast(`已创建项目「${p.name}」`);
    } catch (e) {
      toast('创建失败: ' + e.message);
    } finally {
      createOk.disabled = false;
    }
  };

  trigger.addEventListener('click', () => { panel.hidden ? open() : close(); });
  newBtn.addEventListener('click', () => {
    if (panel.hidden) {
      renderList();
      panel.hidden = false;
      root.classList.add('is-open');
      trigger.setAttribute('aria-expanded', 'true');
    }
    startCreate();
  });
  listEl.addEventListener('click', (e) => {
    const opt = e.target.closest('.picker-option');
    if (opt) pick(opt.dataset.value, opt.textContent);
  });
  createOk.addEventListener('click', create);
  createInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); create(); }
  });
  root.addEventListener('keydown', (e) => {
    // Esc 只收起浮层, 不能连带把整个弹窗关掉 (弹窗的 Esc 监听在 document 上)。
    if (e.key === 'Escape' && !panel.hidden) { e.stopPropagation(); close(); trigger.focus(); return; }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    if (panel.hidden) {
      open();
      const first = listEl.querySelector('.picker-option');
      if (first) first.focus();
      return;
    }
    const opts = [...listEl.querySelectorAll('.picker-option')];
    const i = opts.indexOf(document.activeElement);
    const next = opts[Math.min(Math.max(i + (e.key === 'ArrowDown' ? 1 : -1), 0), opts.length - 1)];
    if (next) next.focus();
  });

  sync();
  _projectPickers.push({ root, sync, close });
}

// 一次性把弹窗里的项目选择器都接上。必须在 innerHTML 写入之后调用。
function initProjectPickers(scope) {
  (scope || document).querySelectorAll('[data-picker]').forEach(bindProjectPicker);
}

// 项目列表变了 (新建/归档/首次加载), 让所有已挂载的选择器重画一遍。
function refreshProjectPickers() {
  for (let i = _projectPickers.length - 1; i >= 0; i--) {
    const p = _projectPickers[i];
    // 弹窗内容每次打开都是新 DOM, 旧的 root 已经脱离文档, 顺手清掉。
    if (!p.root.isConnected) { _projectPickers.splice(i, 1); continue; }
    p.sync();
  }
}

// 点浮层外面收起。只注册一次 document 级监听, 遍历所有打开的选择器 ——
// 每个选择器各挂一个监听的话, 弹窗开合几次就会攒下一堆闭包。
document.addEventListener('pointerdown', (e) => {
  _projectPickers.forEach((p) => {
    if (p.root.isConnected && !p.root.contains(e.target)) p.close();
  });
}, true);

// 「预计用时」随作业类型自动预填 (2026-10-02 用户需求)。
//
// 口径: **只在输入框为空时**才填, 家长已经敲进去的数字绝不覆盖。
// 覆盖手输值是这类"智能预填"最容易被投诉的地方 —— 家长刚填 40,
// 改了下类型变成 30, 他会以为系统改了他的输入。
//
// 触发时机是 input 而非 change: 类型框是可自由输入的(datalist 只是
// 候选), 边打边匹配才对; 只监听 change 的话, 家长选完候选要等失焦
// 才看到数字变化, 像是没生效。
//
// @param estInput   预计用时输入框
// @param typeInput  作业类型输入框
// @param subjectInput 科目控件, 用于类型没命中时退回科目基线
function bindEstAutofill(estInput, typeInput, subjectInput) {
  if (!estInput || !typeInput) return;
  // 关键: 分辨"这个数字是我填的"和"这个数字是家长敲的"。
  // 只用 `value !== ''` 判断是不够的 —— 首次预填后框里就有数字了,
  // 于是家长从"背诵"改成"订正"时会被当成手输值而拒绝更新, 数字永远
  // 停在第一次的结果(实测 背诵=15 改订正=25 却仍显示 15)。
  // 所以自己填的时候打标记, 家长一动就撤掉标记。
  //
  // 撤掉标记后要立刻按新类型重填: 家长主动清空再选类型, 意图是
  // "重新按这个类型给一个默认值", 而不是"从此不再自动填"。否则清空
  // 一次之后预填就永久失灵, 只能手动敲 —— 这与需求原意相悖。
  // (需求说的是"如果为空就预填", 清空后正是"为空"。)
  let touched = estInput.value.trim() !== '';
  const apply = () => {
    if (touched) return;              // 家长敲过非空值, 绝不覆盖
    const type = typeInput.value.trim();
    const subject = subjectInput ? subjectInput.value : '';
    // estDefault 内部就是「类型基线 > 科目基线 > 15」的优先级, 与后端
    // _resolve_est_minutes 同口径, 不在这里另写一套。
    const v = estDefault({ task_type: type, subject });
    estInput.value = v == null ? '' : String(v);
  };
  estInput.addEventListener('input', () => {
    // 清空算"交回控制权": touched 复位, 让后续选类型能重新预填。
    touched = estInput.value.trim() !== '';
    if (!touched) apply();
  });
  typeInput.addEventListener('input', apply);
  typeInput.addEventListener('change', apply);
  // 科目也参与兜底: 类型留空时, 换科目应给出该科的基线值。
  if (subjectInput) subjectInput.addEventListener('change', apply);
  apply();   // 打开即按当前科目/类型填一次
}

async function loadBaselines() {
  try {
    const r = await api('GET', '/settings');
    const pick = k => {
      const hit = r.items.find(x => x.key === k);
      if (!hit) return {};
      try {
        const v = JSON.parse(hit.value);
        return (v && typeof v === 'object' && !Array.isArray(v)) ? v : {};
      } catch (e) { return {}; }
    };
    setBaselineCache({ task_type: pick('task_type_baseline_minutes'), subject: pick('subject_baseline_minutes') });
  } catch (e) {
    // 空 catch 是刻意的: 拉不到配置不该阻断解析, estDefault 会退回最后兜底
  }
}

function renderPreviewTable(items, batchId) {
  const result = document.getElementById('parse-result');
  result.innerHTML = `
    <h3>预览 / 编辑 (${items.length} 条)</h3>
    <p class="muted">带 ⚠ 的几行 AI 没能完全读准, 已经高亮出来并且没有勾选, 麻烦你确认一下</p>
    <table class="preview-table">
      <thead><tr>
        <th>勾</th><th>科目</th><th>任务名</th><th>归属日</th>
        <th>选做</th><th>预计用时</th><th>置信</th><th>原文</th><th>操作</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <div class="row">
      <button id="btn-confirm" class="primary">确认入库</button>
      <button id="btn-discard" class="ghost">丢弃</button>
    </div>
  `;
  const tbody = result.querySelector('tbody');
  const batchDate = document.getElementById('entry-date').value;
  items.forEach((it, idx) => {
    const tr = document.createElement('tr');
    tr.classList.add(`confidence-${it.confidence}`); // 视觉差异化
    tr.dataset.idx = idx;
    // AI 没识别出日期时留空并标红, 由家长确认 —— 不能静默填今天。
    // 静默填充会让家长以为"AI 说今天", 实际是系统替他猜的。
    const noAiDate = !it.due_date;
    // 设计 §9 坑 3: 低置信度默认不勾选, 强制家长过目
    const checkedDefault = it.confidence !== 'low' && !noAiDate;
    // 表格不再显示/编辑「截止」列, 但 AI 解析出的 deadline_time 不能丢 ——
    // 「周四早读 8:20 听写」这类信息只有 AI 能提取出来, 家长在表里点不出来的。
    // 存到 data- 属性, collectRows 原样带进入库请求。
    tr.dataset.dl = it.deadline_time || '';
    // data-label 只给窄屏卡片布局用(见 styles.css §26c): 表格变成一张张
    // 卡片后表头就消失了, 每格必须自带标签才认得出是什么。
    // 桌面端不读这个属性, 所以对宽屏零影响。
    tr.innerHTML = `
      <td data-label="入库"><input data-field="checked" type="checkbox" ${checkedDefault ? 'checked' : ''} /></td>
      <td data-label="科目">
        <select data-field="subject">
          <option ${it.subject==='语文'?'selected':''}>语文</option>
          <option ${it.subject==='数学'?'selected':''}>数学</option>
          <option ${it.subject==='英语'?'selected':''}>英语</option>
        </select>
      </td>
      <td data-label="任务"><input data-field="task_name" value="${escapeAttr(it.task_name)}" /></td>
      <td data-label="归属日">
        <input data-field="due_date" type="date" class="js-datepicker ${noAiDate ? 'need-date' : ''}"
               value="${it.due_date || ''}" placeholder="${batchDate || todayStr()}" />
        ${
          // 归属日列只有 ~130px, 而 td 是 nowrap + overflow:hidden ——
          // 原来那句"AI 未识别归属日, 请确认"会被截成"AI 未识别归属日, 请",
          // 半句话比不提示更让人困惑。改成短标签, 完整说明进 title。
          // 颜色已经由 .need-date(红框红底) 传达了, 这里只补文字。
          noAiDate
            ? '<small class="need-date-hint" title="AI 未识别归属日, 请家长确认">待确认</small>'
            : ''
        }
      </td>
      <td data-label="选做"><input data-field="is_optional" type="checkbox" ${it.is_optional ? 'checked' : ''} /></td>
      <td data-label="预计(分)"><input data-field="est_minutes" type="number" min="1" max="180" value="${it.est_minutes || estDefault(it)}" /></td>
      <td class="col-conf" data-label="置信">${confidenceDot(it.confidence)}</td>
      <td class="col-src" data-label="原文"><small title="${escapeAttr(it.source_span || '')}">${escapeAttr(it.source_span || '')}</small></td>
      <td class="col-del"><button class="ghost row-del" aria-label="删除这一行">删</button></td>
    `;
    tr.querySelector('.row-del').addEventListener('click', () => tr.remove());
    tbody.appendChild(tr);
  });
  // 预览表是动态生成的, 必须在这时才绑定 flatpickr ——
  // 否则新出现的日期框仍是原生控件。删行不影响已绑定的实例。
  initDatePickersIn(result);
  result.querySelector('#btn-confirm').addEventListener('click', () => confirmBatch(batchId, result));
  result.querySelector('#btn-discard').addEventListener('click', () => { result.innerHTML = ''; });
}

function escapeAttr(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

// AI 给的截止时间格式不统一: 有时带秒(2026-09-30T08:20:00), 有时不带
// (2026-09-30T08:20)。这里统一补成带秒的 ISO —— 少了会入库失败,
// 多补则拼出 08:20:00:00 这种畸形串。
function normalizeDeadline(v) {
  const s = String(v || '').trim();
  if (!s) return null;
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(s) ? s + ':00' : s;
}

function collectRows(result) {
  const rows = [...result.querySelectorAll('tbody tr')];
  return rows.map(tr => {
    const get = f => tr.querySelector(`[data-field="${f}"]`);
    const due = get('due_date').value;
    // deadline_time 来自 tr.dataset.dl 而非 DOM —— 表格已无「截止」列,
    // 若还用 get('deadline_time').value 会拿到 null 再取属性, 直接抛 TypeError
    // 把整批入库打断, 且报错发生在循环里, 家长只看到"入库失败"。
    // 格式统一交给 normalizeDeadline 处理。
    // 置信度与原文都从具名单元格取, 不用 querySelector('small') ——
    // 那是"第一个 small", 以后任何一行多出个 small 就会读错字段。
    // 置信度标记从 confidenceDot 换成了 conf-dot, 这里必须同步改,
    // 否则 classList 永远匹配不到, 全部落到 medium,
    // 低置信度行的"需核对"提醒会在入库时丢掉。
    const confCell = tr.querySelector('.col-conf .conf-dot');
    const conf = confCell
      ? (confCell.classList.contains('high') ? 'high'
        : confCell.classList.contains('medium') ? 'medium'
        : confCell.classList.contains('low') ? 'low' : 'medium')
      : 'medium';
    return {
      checked: get('checked').checked,
      subject: get('subject').value,
      task_name: get('task_name').value.trim(),
      due_date: due,
      deadline_time: normalizeDeadline(tr.dataset.dl),
      is_optional: get('is_optional').checked,
      est_minutes: parseInt(get('est_minutes').value, 10),
      source_span: (tr.querySelector('.col-src small') || {}).textContent || '',
      confidence: conf,
    };
  }).filter(r => r.task_name && r.checked);
}

async function confirmBatch(batchId, result) {
  const items = collectRows(result);
  if (!items.length) { toast('至少勾选一行'); return; }
  for (const it of items) {
    if (!it.due_date) {
      // AI 未识别日期的行现在留空, 这条校验从死代码变成真正的守卫
      toast('有作业未确认归属日, 请先填写');
      // 按属性值而非 [value] 属性选择: flatpickr 初始化会重写 value 属性,
      // 用 :not([value]) 会漏掉这些行, 家长点了没反应。
      const emptyCell = [...result.querySelectorAll('[data-field="due_date"]')]
        .find(inp => !inp.value);
      // 直接把日历弹开 —— 家长点了"入库"却被拒绝, 至少该立刻能选日期
      if (emptyCell) openDatePicker(emptyCell);
      return;
    }
    if (!it.est_minutes || it.est_minutes < 1 || it.est_minutes > 180) {
      toast('预计用时必须在 1~180 分钟'); return;
    }
  }
  try {
    const r = await api('POST', '/assignments/batch', { batch_id: batchId, items });
    const skipped = r.skipped || [];
    if (skipped.length) {
      // 去重是静默的, 但必须告诉家长跳过了什么 —— 否则"少了几条"会让人以为丢数据
      const names = skipped.slice(0, 3).map(s =>
        `${s.subject || TASK_KIND_LABELS[s.task_kind] || ''}·${s.task_name}`).join('、');
      const more = skipped.length > 3 ? ` 等 ${skipped.length} 条` : '';
      toast(`已入库 ${r.created_ids.length} 条, 跳过重复 ${skipped.length} 条: ${names}${more}`, 6000);
    } else {
      toast(`已入库 ${r.created_ids.length} 条`);
    }
    result.innerHTML = '';
    rawTextarea.value = '';
    syncCharCount();
    switchTab('board');
  } catch (e) { toast('入库失败: ' + e.message); }
}

// 手动添加 modal
function openManualAddModal() {
  // 培训/习惯任务要选项目, 这里直接用快速添加已缓存的 _qaProjects;
  // 缓存为空时异步补一轮, 不阻塞弹窗打开。
  if (!_qaProjects.length) refreshQuickAddData();
  const m = document.getElementById('modal');
  m.classList.remove('hidden');
  document.getElementById('modal-title').textContent = '手动添加作业';
  document.getElementById('modal-content').innerHTML = `
    <div class="form-grid">
      <label>
        <span class="form-label">归属日</span>
        <input id="m-due" type="date" class="js-datepicker" value="${todayStr()}" />
      </label>
      <label>
        <span class="form-label">任务来源</span>
        <select id="m-kind">
          <option value="subject">学科作业</option>
          <option value="training">培训</option>
          <option value="habit">习惯</option>
        </select>
      </label>
      <label id="m-subject-wrap">
        <span class="form-label">科目</span>
        <select id="m-subject"><option>语文</option><option>数学</option><option>英语</option></select>
      </label>
      <label id="m-project-wrap" hidden>
        <span class="form-label">项目</span>
        <span class="form-field">
          ${projectPickerHtml('m-project')}
          <span class="form-hint">没有项目？点「＋新建项目」当场建一个</span>
        </span>
      </label>
      <label>
        <span class="form-label">作业项</span>
        <input id="m-name" placeholder="如: 完成知能 P14" />
      </label>
      <label>
        <span class="form-label">作业类型</span>
        <span class="form-field">
          ${taskTypeComboHtml('m-type', '', '背诵 / 订正 / 计算…')}
          <span class="form-hint">决定预计用时的默认值, 可留空</span>
        </span>
      </label>
      <label>
        <span class="form-label">预计用时</span>
        <span class="form-field">
          <input id="m-est" type="number" min="1" max="180" inputmode="numeric" placeholder="按作业类型自动预填" />
          <span class="form-hint">留空时按设置里的类型基线自动填</span>
        </span>
      </label>
      <label>
        <span class="form-label">是否选做</span>
        <span class="form-field">
          <span class="form-check">
            <input id="m-opt" type="checkbox" /> <span>选做</span>
          </span>
          <span class="form-hint">默认否 —— 勾选后作业可晚几天补</span>
        </span>
      </label>
      <label>
        <span class="form-label">截止时间</span>
        <span class="form-field">
          <input id="m-dl" type="datetime-local" class="js-datetimepicker" />
          <span class="form-hint">可以不填, 留空表示当天完成</span>
        </span>
      </label>
    </div>
  `;
  const kindSel = document.getElementById('m-kind');
  kindSel.addEventListener('change', () => {
    const isSubject = kindSel.value === 'subject';
    document.getElementById('m-subject-wrap').hidden = !isSubject;
    document.getElementById('m-project-wrap').hidden = isSubject;
  });
  // modal-content 每次打开都是新 DOM, 所以在这里绑定而不是页面启动时
  initDatePickersIn(document.getElementById('modal-content'));
  // 作业类型下拉要在 DOM 建好之后接上, 并排在 bindEstAutofill 之前 ——
  // initCombos 只挂交互, 但顺序上让"先有候选、再有预填"读起来更顺。
  initCombos(document.getElementById('modal-content'));
  // 项目下拉: 自绘控件, 候选与「新建项目」都在浮层里。
  initProjectPickers(document.getElementById('modal-content'));
  // 预计用时留空, 由作业类型自动预填 (2026-10-02 用户需求)。
  // 刻意不给初值: 一旦写死 15, 下面 bindEstAutofill 的"空才填"就永远
  // 不成立, 类型基线配置就永远不生效 —— 那正是 2026-09-29 报障的形态。
  bindEstAutofill(
    document.getElementById('m-est'),
    document.getElementById('m-type'),
    document.getElementById('m-subject'),
  );
  // 显式恢复成"取消 + 确定": 必须调 setModalActions, 不能只赋 onclick ——
  // 家长若先点过"原文"(只读弹窗, 那里隐藏了取消并把确定改成"返回"),
  // 这里不重置就会看到一个没有取消按钮、写着"返回"的提交弹窗。
  setModalActions({
    mode: 'confirm',
    onOk: async () => {
      const dl = document.getElementById('m-dl').value;
      const type = document.getElementById('m-type').value.trim();
      const kind = document.getElementById('m-kind').value;
      const isSubject = kind === 'subject';
      const subject = isSubject ? document.getElementById('m-subject').value : null;
      const pidRaw = document.getElementById('m-project').value;
      const projectId = isSubject || !pidRaw ? null : parseInt(pidRaw, 10);
      // 预计用时改成"可留空"后, 提交时必须兜住空值: 后端 est_minutes 是
      // 必填 (ge=1), 直接把 parseInt('') 的 NaN 发过去会得到 4003, 而
      // 家长看到的只是"入库失败", 完全不知道是哪个框的问题。
      // 兜底口径与自动预填一致(类型 > 科目 > 15), 保证"留空也能存"。
      const estRaw = document.getElementById('m-est').value.trim();
      const est = estRaw === '' ? estDefault({ task_type: type, subject }) : parseInt(estRaw, 10);
      const items = [{
        subject,
        task_kind: kind,
        project_id: projectId,
        task_name: document.getElementById('m-name').value.trim(),
        due_date: document.getElementById('m-due').value,
        // 修正: 原写法 (v || '') + ':00' || null 因 + 优先级高于 ||,
        // 空值会得到字符串 ":00" (truthy), 右侧 null 永远不触发,
        // 后端 pydantic 解析 ":00" 失败 -> 4003, 手动添加不填截止时间必失败。
        deadline_time: dl ? dl + ':00' : null,
        is_optional: document.getElementById('m-opt').checked,
        est_minutes: est,
        // 作业类型一起入库: 不发的话周复盘按类型分组时这条会落进
        // "无类型"组, 而家长明明在弹窗里选过类型。
        task_type: type || null,
        source_span: '(手动添加)',
        confidence: 'high',
      }];
      if (!items[0].task_name) { toast('作业项必填'); return; }
      if (!items[0].due_date) { toast('归属日必填'); return; }
      if (!(est >= 1 && est <= 180)) { toast('预计用时需在 1~180 分钟'); return; }
      try {
        await api('POST', '/assignments/batch', { batch_id: null, items });
        m.classList.add('hidden');
        toast(`已入库 ${items.length} 条`);
        switchTab('board');
      } catch (e) { toast('入库失败: ' + e.message); }
    },
  });
}

// ====== 日期选择器 (flatpickr · 2026-09-28) ======
// 原生 <input type="date"> 在 Windows 上是系统控件: 灰色渐变、系统字体、
// 图标样式不可控, 且和项目奶油糖果配色完全割裂。改用 flatpickr。
//
// 统一走这一个函数初始化, 别处不要各自 new flatpickr —— 配置分散后
// 很快会出现"录入页好看、预览表还是原生控件"的分裂。
const datePickerOptions = {
  // altInput: flatpickr 自己生成可读文本框 (如 "2026-09-28"),
  // 原生 input 隐藏但保持 value 为 ISO 格式 —— collectRows() 仍在读它。
  altInput: true,
  altFormat: 'Y-m-d',
  dateFormat: 'Y-m-d',
  locale: 'zh',            // 周一起始 + 中文星期/月份
  allowInput: true,        // 允许直接键入 2026-09-28
  // 不锁 min/max: 家长要能补录往年作业
};

// 已初始化控件的 flatpickr 实例。
//
// 库不会把实例挂在元素上(4.6.13 源码里 _fpFlatpickr 出现 0 次),
// 也不提供 getElementById / DOM 命名空间反查, 所以只能由我们
// 在初始化时自己留存 —— 这是唯一可靠的方式。
function initDatePicker(input, opts) {
  if (!input || input._fpInstance) return input._fpInstance;
  const inst = flatpickr(input, opts);
  input._fpInstance = inst;
  return inst;
}

// 截止时间用带时分的同一个组件, 避免表里一半奶油一半系统灰。
const dateTimePickerOptions = {
  ...datePickerOptions,
  enableTime: true,
  time_24hr: true,
  dateFormat: 'Y-m-d\\TH:i',     // 与 collectRows 里 dlRaw + ':00' 的拼法对齐
  altFormat: 'Y-m-d H:i',
  minuteIncrement: 5,
};

// 扫描任意容器里的日期/时间框并初始化。
// 解析预览表是动态生成的, 每次渲染后都要重跑, 否则新行的日期框
// 依然是原生控件 —— 这是接入 flatpickr 时最容易漏的一环。
//
// 过滤条件是 type 仍为 date / datetime-local: 库初始化后会把原 input
// 改成 type=hidden, 并另建一个同样带 js-datepicker 类的 type=text 可见框。
// 只按 js-datepicker 选会把这两个都扫到, 导致每次扫描多生成一组控件。
function initDatePickersIn(root) {
  const scope = root || document;
  if (!scope.querySelectorAll) return;
  scope.querySelectorAll('input.js-datepicker').forEach(input => {
    if (input.type === 'date') initDatePicker(input, datePickerOptions);
  });
  scope.querySelectorAll('input.js-datetimepicker').forEach(input => {
    if (input.type === 'datetime-local') initDatePicker(input, dateTimePickerOptions);
  });
}

// 打开某个原生 input 对应的日历。altInput 模式下原生框已被隐藏,
// 焦点必须落在库生成的可见框上, 否则家长点了没反应。
function openDatePicker(input) {
  const inst = input && input._fpInstance;
  if (!inst) return false;
  if (inst.altInput && inst.altInput.focus) inst.altInput.focus();
  if (typeof inst.open === 'function') inst.open();
  return true;
}

// 录入页的批次级默认归属日。
// 顺序要紧: 先赋值再初始化组件。反过来的话, flatpickr 已把原生框改成
// type=hidden, 直接写 .value 不会再同步到可见框, 日历打开会是空白。
const entryDate = document.getElementById('entry-date');
entryDate.value = todayStr();

if (window.flatpickr && window.zh) {
  // 中文包默认周日起始, 但日历页的月历是周一起始(后端 first_weekday
  // 按 Monday=0 返回)。两个日历起点不一致会让家长以为看错了,
  // 这里统一成周一起始, 且必须在 localize 之前改。
  window.zh.firstDayOfWeek = 1;
  flatpickr.localize(window.zh);
  initDatePickersIn(document);   // 含 entry-date
}

// ====== 日历 (月历 + 当日详情) ======
// 当前展示的月份 (null = 本月) 与选中的日期。
// 记下来是为了切 Tab 回来时保留用户翻到的那个月, 而不是每次都弹回本月。
let _calYear = null;
let _calMonth = null;
let _calSelected = null;
let _calData = null;
// 日历查看模式 (2026-10-30): 'completion' 按必做收尾状态上色,
// 'heat' 按当天总用时上色 (越久越红)。两种模式共用同一份 _calData,
// 切模式不发请求, 只重画格子 —— 数据里早就有 actual_total 了。
let _calMode = 'completion';

// 用时热力图分档阈值 (分钟)。参考图的 YlOrRd 色系: 浅黄 -> 琥珀 -> 橙 -> 砖红。
// 取值按真实数据定 (日用时 20~278 分, 中位数 135): 60 / 100 / 140 / 180
// 分别落在 1 小时 / 1.5 小时 / 2.3 小时 / 3 小时, 是家长有直觉的节点。
// 分档必须与 styles.css 的 .heat-l1 ~ .heat-l5 和页面图例逐档对应。
const HEAT_EDGES = [60, 100, 140, 180];
function heatLevel(minutes) {
  if (!minutes || minutes <= 0) return 0;          // 没投入时间: 不上色
  for (let i = 0; i < HEAT_EDGES.length; i++) {
    if (minutes < HEAT_EDGES[i]) return i + 1;
  }
  return HEAT_EDGES.length + 1;
}

function pad2(n) { return String(n).padStart(2, '0'); }

function dateStr(y, m, d) {
  return `${y}-${pad2(m)}-${pad2(d)}`;
}

// 本地时区的"今天"。同样不能用 toISOString(), 见 todayStr() 注释。
function todayParts() {
  const d = new Date();
  return { y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate() };
}

function shiftMonth(y, m, delta) {
  const total = y * 12 + (m - 1) + delta;
  return { y: Math.floor(total / 12), m: (total % 12) + 1 };
}

// 日期格子的完成状态。与看板口径一致: 只看必做, 跳过算已收尾。
function dayState(day) {
  if (!day || !day.total) return 'none';
  if (day.required === 0) return 'optional-only';   // 当天只有选做
  if (day.required_done === day.required) return 'all-done';
  if (day.settled > 0) return 'partial';
  return 'has';
}

async function loadCalendar() {
  const grid = document.getElementById('cal-grid');
  const title = document.getElementById('cal-title');
  const t = todayParts();
  if (_calYear == null) {
    _calYear = t.y;
    _calMonth = t.m;
    _calSelected = _calSelected || dateStr(t.y, t.m, t.d);
  }
  grid.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const r = await api('GET', `/assignments/calendar?year=${_calYear}&month=${_calMonth}`);
    _calData = r;
    title.textContent = `${r.year} 年 ${r.month} 月`;
    renderCalendar();
  } catch (e) {
    grid.innerHTML = `<div class="error-banner">日历加载失败: ${e.message}</div>`;
  }
}

function renderCalendar() {
  const grid = document.getElementById('cal-grid');
  const r = _calData;
  if (!r) return;
  // 模式按钮与图例的选中态跟着 _calMode 走 (切月/切 Tab 回来都保持一致)
  syncCalendarMode();
  // 记住焦点格子: 下面整块重建会销毁当前聚焦的按钮, 浏览器会把焦点
  // 交给详情区里第一个可聚焦控件 (某条作业的"实耗"输入框) 并把它滚进
  // 视野 —— 表现为"点一下日期, 页面却跳到底部", 家长紧接着敲的数字还会
  // 落进那条作业的时间框里。重建完把焦点还回原格子, 既不跳也不丢键盘可用性。
  const activeDate = document.activeElement && document.activeElement.closest
    ? (document.activeElement.closest('.cal-cell') || {}).dataset
    : null;
  const keepFocus = activeDate ? activeDate.date : null;
  grid.innerHTML = '';
  const heat = _calMode === 'heat';
  // 周一起始: 后端 first_weekday 是 Monday=0, 与 cal-weekdays 表头顺序一致。
  for (let i = 0; i < r.first_weekday; i++) {
    grid.appendChild(el('<div class="cal-cell blank"></div>'));
  }
  for (let d = 1; d <= r.days_in_month; d++) {
    const ds = dateStr(r.year, r.month, d);
    const day = r.days[ds];
    const weekday = (r.first_weekday + d - 1) % 7;
    // 当天总用时 (2026-10-15): 与看板概览卡那个数字同口径, 由后端聚合。
    // 0 不显示 —— 还没做完的那天挂一个"0分"只是噪声, 家长扫月历要看的是
    // 已经投入了多少。
    const mins = day ? (day.actual_total || 0) : 0;
    // 底色口径 (2026-10-30 双模式):
    //   completion  按必做收尾状态上色 —— 原有口径, 看"做没做完"
    //   heat        按当天总用时上色 —— 越久越红, 看"投入了多少"
    const lvl = heat ? heatLevel(mins) : 0;
    const cellCls = heat
      ? (lvl ? `heat heat-l${lvl}` : '')
      : dayState(day);
    // 特殊日角标 (2026-10-26): 假期 / 调休 / 周末。放在格子右上角, 小字 ——
    // 日期数字仍是主角, 角标只补一句"那天为什么不一样"(为什么没作业、
    // 为什么周六还要上课)。与作业有无无关, 所以取的是后端另给的 special 表。
    const sp = (r.special || {})[ds];
    const mark = sp
      ? `<span class="cal-mark cal-mark-${escapeAttr(sp.kind)}" title="${escapeAttr(sp.name)}">${escapeAttr(sp.label)}</span>`
      : '';
    // 热力图模式下把用时写进 aria-label: 颜色深浅屏幕阅读器读不出来,
    // 得有一句可读的等价描述。
    const heatLabel = heat && mins > 0 ? ` 用时 ${mins} 分` : '';
    const cell = el(`
      <button class="cal-cell ${cellCls} ${ds === _calSelected ? 'selected' : ''} ${weekday >= 5 ? 'we' : ''}"
              data-date="${ds}" aria-label="${ds}${heatLabel}${sp ? ' ' + escapeAttr(sp.name) : ''}">
        <span class="cal-day">${d}</span>
        ${mark}
        ${day && day.total ? `<span class="cal-badge">${day.required ? `${day.required_done}/${day.required}` : '选'}</span>` : ''}
        ${mins > 0 ? `<span class="cal-time">${mins}分</span>` : ''}
      </button>
    `);
    cell.addEventListener('click', () => selectDate(ds));
    grid.appendChild(cell);
  }
  // 焦点还回同一个日期的新按钮上 (元素已换新, 必须重新查)。
  // 用 preventScroll: 让浏览器只改焦点, 不要再"顺手"把它滚进视野。
  if (keepFocus) {
    const again = grid.querySelector(`.cal-cell[data-date="${keepFocus}"]`);
    if (again) again.focus({ preventScroll: true });
  }
}

// 模式切换的视觉同步: 按钮选中态 + 图例显隐。只碰 DOM, 不碰数据。
function syncCalendarMode() {
  document.querySelectorAll('.cal-mode-btn').forEach(b => {
    const on = b.dataset.calMode === _calMode;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const cl = document.getElementById('cal-legend-completion');
  const hl = document.getElementById('cal-legend-heat');
  if (cl) cl.hidden = _calMode !== 'completion';
  if (hl) hl.hidden = _calMode !== 'heat';
}

function setCalendarMode(mode) {
  if (mode !== 'completion' && mode !== 'heat') return;
  if (mode === _calMode) return;
  _calMode = mode;
  // 数据已加载就直接重画, 不重新请求 —— 两种口径都在同一份响应里。
  if (_calData) renderCalendar();
}

document.querySelectorAll('.cal-mode-btn').forEach(b => {
  b.addEventListener('click', () => setCalendarMode(b.dataset.calMode));
});

async function selectDate(ds) {
  _calSelected = ds;
  renderCalendar();
  await loadList(ds);
}

function loadList(dateArg) {
  const d = dateArg || _calSelected;
  if (!d) return Promise.resolve();
  const root = document.getElementById('list-content');
  const title = document.getElementById('list-detail-title');
  const weekdayLabel = '日一二三四五六'[new Date(d + 'T00:00:00').getDay()];
  title.textContent = `${d} 周${weekdayLabel}${d === todayStr() ? ' (今天)' : ''}`;
  root.innerHTML = '<p class="muted">加载中…</p>';
  return api('GET', `/assignments?date=${d}`)
    .then(r => renderDayDetail(root, d, r.items))
    .catch(e => { root.innerHTML = `<div class="error-banner">加载失败: ${e.message}</div>`; });
}

function renderDayDetail(root, d, items) {
  root.innerHTML = '';
  if (!items.length) {
    root.appendChild(el(`
      <div class="empty">
        <div class="empty-icon">📋</div>
        <p>${d} 没有作业</p>
      </div>
    `));
    return;
  }
  // 分区与看板一致: 待完成 / 已收尾 / 选做折叠。
  // 历史日期也允许改状态 —— 家长常在第二天补记, 不该被限制。
  const isSettled = i => i.status === 'done' || i.status === 'skipped';
  const required = items.filter(i => !i.is_optional);
  const optional = items.filter(i => i.is_optional);
  const pending = required.filter(i => !isSettled(i));
  const settled = required.filter(isSettled);

  // 提示条与看板同源 (2026-09-29): 同一份数据在两个界面给出不同的话,
  // 家长会以为其中一边算错了。isToday 由 d 与今天比较得出, 不能写死。
  // 2026-10-29: 全部做完时走庆祝横幅, 与看板一致 —— 位置也一样放在
  // 进度卡上方, 家长一眼先看到"这天做完了", 再往下核对数字。
  if (pending.length || required.length) {
    const estRemaining = pending.reduce((s, i) => s + (i.est_minutes || 0), 0);
    const hint = boardHint({
      isToday: d === todayStr(),
      pending: pending.length,
      done: settled.filter(i => i.status === 'done').length,
      skipped: settled.filter(i => i.status === 'skipped').length,
      estRemaining,
    });
    const hintEl = hintBlock(hint);
    if (hintEl) root.appendChild(hintEl);
  }

  root.appendChild(el(`<div class="card">
    <div class="progress-text">
      <span>必做已收尾 <strong>${settled.length}</strong> / ${required.length}</span>
      <span class="muted">完成 ${required.filter(i => i.status === 'done').length} · 选做 ${optional.length}</span>
    </div>
  </div>`));

  const section = (title, list) => {
    if (!list.length) return;
    const doneN = list.filter(i => i.status === 'done').length;
    const c = el(`<div class="card"><div class="card-title">${title}<span class="count">${doneN}/${list.length}</span></div><div class="card-body"></div></div>`);
    list.forEach(item => c.querySelector('.card-body').appendChild(renderAssignment(item, true, refreshCalendarDetail, _focusActualId)));
    root.appendChild(c);
  };
  section('待完成', pending);
  section('已收尾', settled);

  // 选做分组与看板同源 (2026-10-29): 复用 optionalGroupCard, 全做完
  // 时同样折叠成一行。同一份数据两个界面表现不同, 家长会怀疑其中
  // 一边显示错了。
  if (optional.length) {
    root.appendChild(optionalGroupCard(optional, refreshCalendarDetail));
  }
}

// 历史日期改状态后要同时刷新日历格子, 但不整页重载,
// 否则家长点一下"完成"就被弹回日历页顶部。
// 必须 return Promise: patchStatus 靠 await 它来决定何时清空
// _focusActualId, 不返回的话 loadList 的异步渲染还没跑, 标志就被清掉了。
function refreshCalendarDetail() {
  const cal = _calData
    ? api('GET', `/assignments/calendar?year=${_calYear}&month=${_calMonth}`)
        .then(r => { _calData = r; renderCalendar(); })
        .catch(() => {})
    : Promise.resolve();
  return Promise.all([cal, loadList(_calSelected)]);
}

function gotoDate(y, m, day) {
  _calYear = y;
  _calMonth = m;
  _calSelected = dateStr(y, m, day);
  loadCalendar();
  loadList(_calSelected);
}

document.getElementById('cal-prev').addEventListener('click', () => {
  const n = shiftMonth(_calYear, _calMonth, -1);
  _calYear = n.y; _calMonth = n.m;
  loadCalendar();
});
document.getElementById('cal-next').addEventListener('click', () => {
  const n = shiftMonth(_calYear, _calMonth, 1);
  _calYear = n.y; _calMonth = n.m;
  loadCalendar();
});
document.getElementById('cal-today').addEventListener('click', () => {
  const t = todayParts();
  gotoDate(t.y, t.m, t.d);
});
document.getElementById('btn-load-today').addEventListener('click', () => {
  const t = todayParts();
  gotoDate(t.y, t.m, t.d);
});
document.getElementById('btn-load-yesterday').addEventListener('click', () => {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  gotoDate(d.getFullYear(), d.getMonth() + 1, d.getDate());
});

// ====== 复盘 ======
// 统计周期: 本周 / 上周 / 本月。period 的取值与后端一致
// (app/routers/analytics.py 的 PERIOD_PATTERN), 别处不要另起一套名字。
const PERIOD_LABEL = { current: '本周', last: '上周', month: '本月' };

// 当前选中的周期。切走再切回时要用同一个周期重画, 否则高亮的按钮
// 与画出来的数据对不上 (点「本月」-> 去看板 -> 回来, 按钮还亮着「本月」,
// 但画的是「本周」)。
let _reviewPeriod = 'current';

// ====== 周末显示开关 (2026-10-30) ======
// 默认不展示。作业都在上学最后一天 (通常周五) 布置, 周末既没有作业,
// 也不存在"上了学却没写"的情况 —— 留着只是两根空柱和一张 0 分钟的卡,
// 看着像漏统计, 实际什么也没有。
//
// 只影响前端展示: 后端的统计口径一律不动 (平日日均、偏差表、占比饼图的
// 算法与数值都不变), 只是不把周末那两天画出来。
//
// 存 localStorage 而不是后端设置项: 这是纯显示偏好, 放后端的话设置页会
// 多一行看不懂的 true/false 文本框, 还得多一套 bool 类型的校验。
const SHOW_WEEKEND_KEY = 'review_show_weekend';

function showWeekend() {
  try {
    return localStorage.getItem(SHOW_WEEKEND_KEY) === '1';
  } catch (e) {
    return false;  // 隐私模式等禁用 localStorage 时退回默认
  }
}

function setShowWeekend(on) {
  try {
    localStorage.setItem(SHOW_WEEKEND_KEY, on ? '1' : '0');
  } catch (e) {
    // 存不了就算了, 只是这次不记住 —— 不该因此报错挡住页面
  }
}

// 切换统计周期。按钮高亮与数据必须一起走这里 —— 以前高亮由点击事件
// 单独改, 而 switchTab 进页面时又固定 loadReview('current'), 两者会
// 脱节: 家长选了「本月」, 切去看板再回来, 数据变成「本周」而按钮还亮着
// 「本月」。
function setReviewPeriod(period) {
  _reviewPeriod = period;
  document.querySelectorAll('.period-btn').forEach(x => {
    const on = x.dataset.period === period;
    x.classList.toggle('primary', on);
    x.classList.toggle('ghost', !on);
  });
  loadReview(period);
}

document.querySelectorAll('.period-btn').forEach(b => {
  b.addEventListener('click', () => setReviewPeriod(b.dataset.period));
});

// 周末开关: 勾上 = 画出周末那两天。切换后按当前周期重画。
const weekendBox = document.getElementById('chk-weekend');
if (weekendBox) {
  const syncWeekendToggle = () => {
    weekendBox.checked = showWeekend();
    // 勾上后文字加深 (不用 :has(), 那在老浏览器里整条规则失效)
    weekendBox.closest('.weekend-toggle')?.classList.toggle('is-on', weekendBox.checked);
  };
  syncWeekendToggle();
  weekendBox.addEventListener('change', () => {
    setShowWeekend(weekendBox.checked);
    syncWeekendToggle();
    loadReview(_reviewPeriod);
  });
}

async function loadReview(period) {
  _reviewPeriod = period;
  const root = document.getElementById('review-content');
  // 必须清空: 下面几块是 appendChild 进去的, 不会自动顶掉旧内容。
  // 不清的话点「上周」会在旧的一周下面再接一周, 页面越点越长。
  // 这里不用 innerHTML 塞「加载中…」占位 —— 两条接口并发返回后追加,
  // 那个占位会一直留在最上面 (appendChild 顶不掉它), 曾因此在页面上
  // 留着一行永远不消失的"加载中…"。
  root.innerHTML = '';
  // 周报固定是本周, 不随上面的周期按钮变 —— 它回答"这周过得怎么样",
  // 是整体结论; 下面两块才是可以来回切换的统计区间。
  // 两条接口并发, 谁先回来谁先渲染。用时统计不受偏差表"样本不足"的影响
  // (见 /analytics/review-time 的说明), 所以顺序不能反过来等 —— 先 await
  // 偏差表的话, 4011 一抛就把整个 await 链带走, 上面这块也渲染不出来。
  const timeP = loadReviewTime(period).catch(
    e => el(`<div class="error-banner">用时统计加载失败: ${e.message}</div>`));
  const devP = loadReviewDeviation(period);
  root.appendChild(await timeP);
  root.appendChild(await devP);
}

//
// 周报单独 fetch, 且自己吞掉异常: 它失败不该让下面的用时/偏差也白屏。
// 文案原则 (商业计划 §5.7): 只跟孩子自己的历史比, 不评价天赋、不排名;
// 建议最多 3 条 (1–2 年级 2 条), 先当下有事再下次能改。
  try {
    const r = await api('GET', '/weekly-report');
    return renderWeeklyReport(r);
  } catch (e) {
    return el(`<div class="error-banner">周报加载失败: ${e.message}</div>`);
  }
}

function renderWeeklyReport(r) {
  const c = r.completion;
  const t = r.time;
  const b = r.blockers;
  const tr = r.training;
  const ld = r.load;
  const tn = r.trend;

  const card = el(`
    <div class="card report-card">
      <div class="report-head">
        <div class="report-head-main">
          <div class="report-kicker">本周课后周报${r.mode === 'lite' ? ' · 低年级版' : ''}</div>
          <div class="report-range">${r.week_start.slice(5)} ~ ${r.week_end.slice(5)}</div>
        </div>
        <button class="ghost report-export">导出本周清单</button>
      </div>
      <div class="report-stats">
        <div class="report-stat"><strong>${c.rate}%</strong><span>完成率</span></div>
        <div class="report-stat"><strong>${c.settled}/${c.required}</strong><span>必做收尾</span></div>
        <div class="report-stat"><strong>${t.total_minutes}</strong><span>作业分钟</span></div>
        <div class="report-stat"><strong>${t.avg_daily_minutes}</strong><span>上学日日均</span></div>
      </div>
    </div>
  `);

  // 完成情况: 只列没收尾的必做项, 最多 6 条 (再多就不是"清单"而是账本)。
  if (c.unfinished.length) {
    const block = el(`<div class="report-block">
      <div class="report-block-title">还没收尾 <span class="muted">${c.pending} 项</span></div>
    </div>`);
    const ul = el('<ul class="report-list"></ul>');
    c.unfinished.slice(0, 6).forEach(u => {
      const who = u.subject || u.project_name || '';
      ul.appendChild(el(`<li>${escapeAttr(u.weekday)}${who ? ' · ' + escapeAttr(who) : ''} · ${escapeAttr(u.task_name)}</li>`));
    });
    block.appendChild(ul);
    card.appendChild(block);
  }

  // 时间结构: 分科条只画学科任务; 训练线单独一块, 两者不混在一起。
  const subjects = orderedSubjects(t.by_subject);
  if (subjects.length) {
    const block = el(`<div class="report-block">
      <div class="report-block-title">时间花在哪 <span class="muted">共 ${t.total_minutes} 分钟</span></div>
    </div>`);
    subjects.forEach(s => {
      const pct = t.subject_share[s] || 0;
      block.appendChild(el(`<div class="report-bar-row">
        <span class="report-bar-label">${escapeAttr(s)}</span>
        <span class="report-bar-track"><i style="width:${pct}%;background:${subjectColor(s)}"></i></span>
        <span class="report-bar-val">${t.by_subject[s]} 分</span>
      </div>`));
    });
    block.appendChild(el(`<div class="muted report-foot">政策 60 分钟只是参考线, 周报只看孩子自己的变化</div>`));
    card.appendChild(block);
  }

  // 估时偏差: 样本不足是正常状态, 不让它挡住整页。
  if (r.deviation.note) {
    card.appendChild(el(`<div class="report-block"><div class="report-block-title">估时偏差</div>
      <div class="muted">${escapeAttr(r.deviation.note)}</div></div>`));
  } else if (r.deviation.top_underestimate.length || r.deviation.top_overestimate.length) {
    const block = el(`<div class="report-block">
      <div class="report-block-title">估时偏差 <span class="muted">样本 ${r.deviation.sample_size} 条</span></div>
    </div>`);
    r.deviation.top_underestimate.slice(0, 2).forEach(x => {
      block.appendChild(el(`<div class="report-dev-row warn">${escapeAttr(x.subject)}${escapeAttr(x.task_type)} 平均超 ${x.diff_minutes} 分钟</div>`));
    });
    r.deviation.top_overestimate.slice(0, 2).forEach(x => {
      block.appendChild(el(`<div class="report-dev-row">${escapeAttr(x.subject)}${escapeAttr(x.task_type)} 平均少 ${Math.abs(x.diff_minutes)} 分钟</div>`));
    });
    card.appendChild(block);
  }

  // 卡点: 三问里的难/烦/不专注, 加上实际远超预估的任务。
  const chips = [];
  if (b.hard_days.length) chips.push(`偏难 ${b.hard_days.length} 天`);
  if (b.bad_focus_days.length) chips.push(`不专注 ${b.bad_focus_days.length} 天`);
  if (b.bad_mood_days.length) chips.push(`情绪差 ${b.bad_mood_days.length} 天`);
  if (chips.length || b.slow_tasks.length) {
    const block = el('<div class="report-block"><div class="report-block-title">卡点</div></div>');
    if (chips.length) {
      const row = el('<div class="report-chips"></div>');
      chips.forEach(txt => row.appendChild(el(`<span class="report-chip">${txt}</span>`)));
      block.appendChild(row);
    }
    b.slow_tasks.forEach(x => {
      block.appendChild(el(`<div class="report-slow">${escapeAttr(x.task_name)} 实际 ${x.actual_minutes} 分 / 预估 ${x.est_minutes} 分 (${x.ratio} 倍)</div>`));
    });
    card.appendChild(block);
  }

  // 训练线: 家长最想知道"练了几次、练到哪"。
  if (tr.logs > 0) {
    const block = el(`<div class="report-block">
      <div class="report-block-title">训练线 <span class="muted">${tr.logs} 次 · ${tr.total_minutes} 分钟</span></div>
    </div>`);
    tr.by_project.forEach(p => {
      block.appendChild(el(`<div class="report-train">
        <strong>${escapeAttr(p.project_name || '训练')}</strong>
        <span class="muted">${p.logs} 次 · ${p.total_minutes} 分钟</span>
        ${p.last_segment ? `<span class="report-train-seg">最近: ${escapeAttr(p.last_segment)}</span>` : ''}
      </div>`));
    });
    card.appendChild(block);
  }

  // 负荷: 只在本周真排过课/计划时才有意义。
  const loadBits = [];
  if (ld.fullest_day) loadBits.push(`最满 ${ld.fullest_day.weekday} ${ld.fullest_day.load_minutes} 分钟`);
  if (ld.overloaded_days) loadBits.push(`超载 ${ld.overloaded_days} 天`);
  if (ld.conflict_days) loadBits.push(`课表冲突 ${ld.conflict_days} 天`);
  if (loadBits.length) {
    card.appendChild(el(`<div class="report-block"><div class="report-block-title">本周负荷</div>
      <div class="report-load">${loadBits.map(x => `<span class="report-chip">${x}</span>`).join('')}</div></div>`));
  }

  // 趋势: 只跟上周比, 不跟别人比。
  if (tn.prev_done > 0 || c.done > 0) {
    const bits = [];
    if (tn.done_delta) bits.push(`完成项 ${tn.done_delta > 0 ? '+' : ''}${tn.done_delta}`);
    if (tn.minutes_delta) bits.push(`用时 ${tn.minutes_delta > 0 ? '+' : ''}${tn.minutes_delta} 分钟`);
    if (bits.length) {
      card.appendChild(el(`<div class="report-block"><div class="report-block-title">和上周比</div>
        <div class="muted">${bits.join(' · ')}</div></div>`));
    }
  }

  if (r.praise.length) {
    card.appendChild(el(`<div class="report-praise"><span>给孩子</span>${escapeAttr(r.praise.join('；'))}</div>`));
  }
  }
  if (r.suggestions.length) {
    const block = el('<div class="report-block"><div class="report-block-title">今晚可以试</div></div>');
    const ul = el('<ul class="report-list report-suggest"></ul>');
    r.suggestions.forEach(s => ul.appendChild(el(`<li>${escapeAttr(s)}</li>`)));
    block.appendChild(ul);
    card.appendChild(block);
  }

  card.querySelector('.report-export').addEventListener('click', () => {
    window.location.href = `${API}/export/assignments.csv?date=${r.week_start}`;
  });
  return card;
}


// 偏差表单独包一层: 它有"样本不足"这个正常分支, 不该把异常抛到调用方,
// 否则上面已经渲染好的用时统计会被一起清掉。
async function loadReviewDeviation(period) {
  try {
    const r = await api('GET', `/analytics/review?period=${period}`);
    const box = el(`<div class="review-deviation">
      <div class="card">
        <strong>${r.period_start} ~ ${r.period_end}</strong>
        <span class="muted"> · 样本 ${r.sample_size} 条</span>
      </div>
    </div>`);
    if (!r.by_type.length) {
      box.appendChild(el(`<div class="muted">${r.note || `${PERIOD_LABEL[period] || '本周'}无完成数据`}</div>`));
    } else {
      renderDeviationTable(box, r);
    }
    return box;
  } catch (e) {
    if (e.message.includes('4011')) {
      return el(`<div class="card muted">样本不足 (需 ≥ 5 条已完成), 暂不生成偏差表</div>`);
    }
    return el(`<div class="error-banner">复盘失败: ${e.message}</div>`);
  }
}

// ====== 每日各科作业用时 (2026-10-15) ======

// 科目色与看板学科用时条同源 (--c-语/--c-数/--c-英), 家长在两个页面
// 看到"橙色就是语文", 不需要重新记一遍映射。
// 顺序复用文件头部的 SUBJECT_ORDER —— 那里的注释已经写明"抽成一处
// 定义免得两处顺序不一致", 复盘页再加一个同样的常量就违背了它。
const SUBJECT_COLORS = {
  语文: 'var(--c-语)', 数学: 'var(--c-数)', 英语: 'var(--c-英)',
};

function subjectColor(name) {
  return SUBJECT_COLORS[name];
}

// 图例/标签用的科目清单。与后端 _ordered 同规则: 只保留 SUBJECT_ORDER
// 里的科目, 顺序不变 —— subject 列有 CHECK 约束, 不会出现表外科目。
function orderedSubjects(bySubject) {
  return SUBJECT_ORDER.filter(s => (bySubject || {})[s] != null);
}

// 一天的档位 -> 说明文案。写在图例里, 家长才知道灰色那几天为什么没柱子。
const DAY_KIND_META = {
  plain:  { label: '平日',   hint: '周一至周四, 作业量最稳定' },
  friday: { label: '周五',   hint: '通常已包含周末的作业量, 单独看' },
  makeup: { label: '调休',   hint: '周末补班, 要上学' },
  off:    { label: '休息',   hint: '不上学, 不计入平均' },
  holiday:{ label: '假期',   hint: '法定假日, 不计入平均' },
};

async function loadReviewTime(period) {
  const r = await api('GET', `/analytics/review-time?period=${period}`);
  const wrap = el('<div class="wt-block"></div>');
  const isMonth = r.period === 'month';
  const label = PERIOD_LABEL[period] || '本周';
  const withWeekend = showWeekend();

  // 周末 (kind='off', 不上学的普通休息日) 在实际使用中几乎没有作业:
  // 作业都在上学最后一天 (通常周五) 布置, 所以周末那两天往往既没数据
  // 也没"上了学却没写"的情况 —— 画出来只是两根空柱 + 一张 0 分钟的卡。
  //
  // 只按 kind 精确过滤, 不按星期几过滤: 调休上班的周六 (kind='makeup')
  // 与法定假期 (kind='holiday') 都落在周六周日, 但它们是有真实作业的上
  // 学日, 藏起来等于丢数据。
  //
  // 统计口径一律不动: 平日日均、占比饼图、偏差表都是后端算好的, 原样用。
  // 这里只决定"哪些天要画出来"。
  const view = withWeekend ? r : {
    ...r,
    days: r.days.filter(d => d.kind !== 'off'),
    excluded_days: (r.excluded_days || []).filter(e => e.kind !== 'off'),
  };

  // ===== 概览卡 =====
  const pa = r.plain_avg || { days: 0, total_minutes: 0, by_subject: {} };
  wrap.appendChild(el(`
    <div class="card wt-head">
      <div class="wt-head-top">
        <div>
          <div class="muted">平日日均 (周一至周四)</div>
          <div class="wt-big"><strong>${pa.total_minutes || 0}</strong><span class="wt-unit">分钟</span></div>
        </div>
        <div class="wt-head-side">
          <div class="muted">上学日</div>
          <div class="wt-side-num">${r.school_day_count}<span class="wt-unit-sm"> 天</span></div>
          <div class="muted">统计区间 ${r.period_start.slice(5)} ~ ${r.period_end.slice(5)}${isMonth ? ' (至今)' : ''}</div>
        </div>
      </div>
      <div class="muted wt-scope">口径: 只算已完成作业 (跳过的不计), 未填实际用时按预估计; 选做计入; 观察作业不计。</div>
    </div>
  `));

  // 平日档只剩 1~2 天时, "日均"其实是一两天的数字换个说法。
  // 国庆周尤其明显: 七天里只有一天上学, 那个"日均"不代表任何常态。
  // 静默显示会让家长把它当稳定基线, 所以必须说出来。
  if (pa.days > 0 && pa.days <= 2) {
    wrap.appendChild(el(`<div class="wt-warn">
      ${label}只有 <strong>${pa.days}</strong> 个平日 (周一至周四), 平日日均只由这 ${pa.days} 天算出,
      参考意义有限 —— 看「分档对照」里的绝对值更稳妥。
    </div>`));
  }

  // ===== 柱状图: 每天各科堆叠 =====
  wrap.appendChild(renderTimeChart(view));

  // ===== 饼图: 各科时间占比 (预计 / 实际) =====
  const pies = renderSubjectShare(view);
  if (pies) wrap.appendChild(pies);

  // ===== 分档卡片: 平日 / 周五 / 周末 / 调休 =====
  wrap.appendChild(renderBuckets(view, withWeekend));

  // ===== 图例 =====
  wrap.appendChild(renderLegend(withWeekend));

  // 被剔除的日子要显式说出来。国庆那周若无提示, 家长会以为少统计了 4 天。
  if (view.excluded_days && view.excluded_days.length) {
    let txt;
    if (isMonth) {
      // 一个月里有十来个不上学的日子, 逐日列出来是噪音 —— 按类型汇总,
      // 具体是哪几天看上面的柱状图 (虚线柱) 就够了。
      const byKind = {};
      view.excluded_days.forEach(e => {
        byKind[e.kind_label] = (byKind[e.kind_label] || 0) + 1;
      });
      txt = Object.entries(byKind).map(([k, v]) => `${k} ${v} 天`).join(' · ');
    } else {
      txt = view.excluded_days
        .map(e => `${e.date.slice(5)} ${e.kind_label}`)
        .join(' · ');
    }
    wrap.appendChild(el(`<p class="muted wt-excluded">已剔除 (不上学): ${txt}</p>`));
  }

  const noData = view.days.every(d => d.total_minutes === 0);
  if (noData) {
    wrap.appendChild(el(`<div class="empty">
      <div class="empty-icon">📊</div>
      <p>该区间没有已完成的作业, 暂无用时数据</p>
    </div>`));
  }
  return wrap;
}

// 每天一根柱子, 柱内按科目堆叠。假期/休息日不画实心柱, 只画一个虚线
// 轮廓 + 斜纹 —— 让"这天没统计"和"这天用时是 0"在视觉上可区分。
function renderTimeChart(r) {
  const W = 700, H = 260;
  const padL = 34, padR = 10, padT = 16, padB = 42;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const days = r.days;
  const n = days.length;
  // 视图密度决定能画多少东西。一周 7 天每格近 94px, 什么都能写;
  // 本月三十来天每格只有 21px —— 星期名、柱内数字、柱顶总数都塞不下,
  // 硬画出来是互相压字, 比不画更难读。所以密集时只留"哪几天高"这个信息。
  const dense = n > 10;
  const labelEvery = dense ? Math.ceil(n / 7) : 1;  // 密集时每隔几天标一个日期
  const maxMin = Math.max(...days.map(d => d.total_minutes), 1);
  // 纵轴上限取"好看的整数", 再由它均分 2 段 —— 直接对 maxMin 取整到 10
  // 会得到 190 这种刻度, 均分后是 190/95/0, 中间那档不是整数, 家长读图
  // 时得自己在脑子里换算。按 1/2/5 阶梯向上取, 两档都落在整数上。
  const step = maxMin <= 50 ? 50 : maxMin <= 100 ? 50 : maxMin <= 200 ? 100
             : maxMin <= 400 ? 200 : Math.ceil(maxMin / 200) * 200;
  const niceMax = Math.max(step, Math.ceil(maxMin / step) * step);
  const slot = plotW / n;
  const barW = Math.min(56, slot * 0.6);

  let g = '';
  // 横向网格线 + y 轴刻度
  for (let i = 0; i <= 2; i++) {
    const val = Math.round(niceMax * (1 - i / 2));
    const y = padT + (plotH * i / 2);
    g += `<line class="wt-grid" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"/>`;
    g += `<text class="wt-axis" x="${padL - 6}" y="${y + 3}" text-anchor="end">${val}</text>`;
  }

  days.forEach((d, i) => {
    const cx = padL + slot * i + slot / 2;
    const x = cx - barW / 2;
    if (!d.is_school_day) {
      // 不上学: 虚线轮廓 + 斜纹, 不给任何"用时"暗示
      const h = Math.max(6, plotH * 0.06);
      g += `<rect class="wt-bar-off" x="${x}" y="${padT + plotH - h}" width="${barW}" height="${h}" rx="4"/>`;
    } else if (d.total_minutes > 0) {
      // 自下而上堆叠。顺序固定 语->数->英 (与后端一致), 而不是按当天
      // 的出现顺序 —— 否则周一黄在底、周二蓝在底, 家长没法把颜色
      // 稳定地对应到科目, 堆叠图就白画了。
      let acc = 0;
      orderedSubjects(d.by_subject).forEach(name => {
        const mins = d.by_subject[name] || 0;
        const h = plotH * (mins / niceMax);
        const y = padT + plotH - (plotH * (acc + mins) / niceMax);
        g += `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(h, 1)}" rx="2"
                 fill="${subjectColor(name)}"><title>${name} ${mins} 分钟</title></rect>`;
        // 段内数字紧贴色块上沿。只在该段够高时画 —— 太窄的段塞数字会
        // 压到相邻段上, 三个数字叠在一起比不画更难读。
        if (h >= 13) {
          // 柱子本身也要够宽: 本月每根柱只有 12px, 写进去的数字会溢出
          // 柱子、盖到隔壁那根上。宽度不够就只留色块 (悬停仍能看到分钟数)。
          if (barW >= 24) {
            g += `<text class="wt-seg" x="${cx}" y="${y + h / 2 + 3.5}"
                   text-anchor="middle">${mins}</text>`;
          }
        }
        acc += mins;
      });
      // 柱顶标总数: 家长最先想看的是"今天总共多少"
      if (!dense) {
        const totalY = padT + plotH - (plotH * acc / niceMax) - 5;
        g += `<text class="wt-total" x="${cx}" y="${Math.max(totalY, 10)}" text-anchor="middle">${d.total_minutes}</text>`;
      }
    } else if (!dense) {
      g += `<text class="wt-zero" x="${cx}" y="${padT + plotH - 6}" text-anchor="middle">0</text>`;
    }
    // x 轴标签: 星期 + 日期。假期额外标出节日名。
    if (!dense) {
      g += `<text class="wt-xlabel" x="${cx}" y="${H - padB + 16}" text-anchor="middle">${d.weekday_label}</text>`;
      g += `<text class="wt-xdate ${d.is_school_day ? '' : 'is-off'}" x="${cx}" y="${H - padB + 30}" text-anchor="middle">${d.date.slice(5)}</text>`;
      if (!d.is_school_day) {
        g += `<text class="wt-xkind" x="${cx}" y="${H - padB + 42}" text-anchor="middle">${escapeAttr(d.kind_label)}</text>`;
      }
    } else if (i % labelEvery === 0) {
      // 密集时只标日号 (每 labelEvery 天一个), 星期名与完整日期都留白 ——
      // 否则 30 个 "周一/周二" 连成一片, 反而看不出哪天是哪天; 完整日期
      // "10-05" 在 21px 宽的格子里也要溢出到隔壁去。
      g += `<text class="wt-xdate ${d.is_school_day ? '' : 'is-off'}" x="${cx}" y="${H - padB + 16}" text-anchor="middle">${Number(d.date.slice(8, 10))}</text>`;
    }
  });

  // 科目图例: 堆叠图的颜色必须能对上名字, 否则"橙色那一段是多少"
  // 只能靠猜。顺序与柱内堆叠一致 (语->数->英)。
  const allSubs = orderedSubjects(
    days.reduce((acc, d) => Object.assign(acc, d.by_subject), {}));
  const legend = allSubs.map(s =>
    `<span class="wt-lg"><i class="wt-lg-dot" style="background:${subjectColor(s)}"></i>${escapeAttr(s)}</span>`
  ).join('');

  return el(`<div class="card">
    <div class="card-title">每日作业用时<span class="count">分钟</span></div>
    <svg class="wt-chart" viewBox="0 0 ${W} ${H}" role="img"
         aria-label="每日各科作业用时堆叠柱状图">${g}</svg>
    ${legend ? `<div class="wt-legend wt-subjects">${legend}</div>` : ''}
  </div>`);
}

// ====== 各科时间占比饼图 (2026-10-29) ======
//
// 为什么预计与实际要分成两张图, 而不是合成一个环:
//   合成的话每个科目占两段, 一眼看过去全是"语文语文语文", 想比较
//   "当初估的分布"与"实际发生的分布"完全无从下手。分开两张, 形状
//   可比, 差在哪一眼就看出来 —— 这才是家长翻这页想看的东西。
//
// 口径与上面逐日用时完全一致 (只算 done、实耗为空按预估计、选做计入、
// 观察作业不计), 所以两个环的合计就是柱状图的总用时, 三处对得上账。
// 统计区间是**整周**: 周末真写了作业的那部分时间也是家长付出的,
// 从占比里抹掉会让"这周时间都去哪了"答非所问。

const PIE_CX = 95, PIE_CY = 95, PIE_R = 68;

function piePoint(cx, cy, r, rad) {
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

// 一块扇形。整圆 (只有一个科目且占满 100%) 必须单独画: 起止点重合时
// SVG 的 A 命令画出来是一条退化直线, 整块饼会凭空消失。
function pieSlice(cx, cy, r, a0, a1, fill, label) {
  const title = `<title>${label}</title>`;
  if (a1 - a0 >= Math.PI * 2 - 1e-6) {
    return `<circle class="wt-pie-slice" cx="${cx}" cy="${cy}" r="${r}" fill="${fill}">${title}</circle>`;
  }
  const [x0, y0] = piePoint(cx, cy, r, a0);
  const [x1, y1] = piePoint(cx, cy, r, a1);
  const large = (a1 - a0) > Math.PI ? 1 : 0;
  const d = `M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} `
          + `A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z`;
  return `<path class="wt-pie-slice" d="${d}" fill="${fill}">${title}</path>`;
}

// 百分比用最大余数法取整, 保证几个数加起来正好 100%。
// 各自四舍五入会得到 33% + 33% + 33% = 99%, 家长会怀疑图画错了。
function sharePercents(mins) {
  const total = mins.reduce((s, m) => s + m, 0);
  if (!total) return mins.map(() => 0);
  const exact = mins.map(m => (m / total) * 100);
  const out = exact.map(x => Math.floor(x));
  let rest = 100 - out.reduce((s, v) => s + v, 0);
  const byRem = exact
    .map((x, i) => ({ i, rem: x - Math.floor(x) }))
    .sort((a, b) => b.rem - a.rem);
  for (let k = 0; rest > 0; k++, rest--) out[byRem[k % byRem.length].i] += 1;
  return out;
}

// 扇形上能放下几行字?
//
// 文字是横排的矩形, 会不会压到相邻扇形, 取决于它在标签半径上张开的
// 角度。张角最大的不是外侧角, 而是**靠圆心那一侧的角** —— 同样的横
// 竖偏移, 离圆心越近张角越大。所以两个方向各算一次取大的:
//   标签在左右两侧时: atan2(半高, 标签半径 - 半宽)
//   标签在上下一侧时: atan2(半宽, 标签半径 - 半高)
// 扇形超过半圈时它已经包住大半张图, 一定放得下。
//
// 分两档是有意的: 科目名是主角, 哪怕放不下百分比也要把名字放上去;
// 实在放不下才只留给图例 —— 挤在一起的字比没有字更难读。
const PIE_LABEL_R = PIE_R * 0.72;   // 标签离圆心的距离
const PIE_LABEL_HALF_W = 12;        // "语文"两字 @12px 的半宽

function pieLabelHalfAngle(halfH) {
  return Math.max(
    Math.atan2(halfH, PIE_LABEL_R - PIE_LABEL_HALF_W),
    Math.atan2(PIE_LABEL_HALF_W, PIE_LABEL_R - halfH)
  );
}
// 单行 (只有科目名) 与双行 (名字 + 百分比) 的门槛
const PIE_ANGLE_ONE = pieLabelHalfAngle(6);    // 单行半高 6
const PIE_ANGLE_TWO = pieLabelHalfAngle(12);   // 双行合起来半高 12

// 返回 0 (放不下) / 1 (只放科目名) / 2 (科目名 + 百分比)
function pieLabelTier(frac) {
  if (frac > 0.5) return 2;
  const half = (frac * Math.PI) / 2;
  if (half >= PIE_ANGLE_TWO) return 2;
  if (half >= PIE_ANGLE_ONE) return 1;
  return 0;
}

// 一张饼。by_subject 为空或合计为 0 时给出空态, 不画空饼 ——
// 一个只有灰色底的圆会被误读成"占比为 0"。
function renderPie(bySubject, total, ariaLabel) {
  const subs = orderedSubjects(bySubject).filter(s => (bySubject[s] || 0) > 0);
  if (!subs.length || !total) {
    return el(`<div class="wt-pie-empty muted">该区间没有已完成的作业</div>`);
  }
  const mins = subs.map(s => bySubject[s] || 0);
  const pcts = sharePercents(mins);

  let slices = '';
  let labels = '';
  let a = -Math.PI / 2;   // 从 12 点方向起画, 顺时针
  subs.forEach((name, i) => {
    const frac = mins[i] / total;
    const a0 = a;
    const a1 = a + frac * Math.PI * 2;
    slices += pieSlice(PIE_CX, PIE_CY, PIE_R, a0, a1, subjectColor(name),
      `${name} ${mins[i]} 分钟 (${pcts[i]}%)`);
    // 扇形里直接写科目名 (+ 百分比), 不用在图例和色块之间来回对照。
    // 放不下就退一档只写名字, 再放不下才只留给图例。
    const tier = pieLabelTier(frac);
    if (tier >= 1) {
      const [lx, ly] = piePoint(PIE_CX, PIE_CY, PIE_LABEL_R, (a0 + a1) / 2);
      const nameY = tier >= 2 ? ly - 6 : ly;
      labels += `<text class="wt-pie-label" x="${lx.toFixed(1)}" y="${nameY.toFixed(1)}"
                 text-anchor="middle" dominant-baseline="middle">${escapeAttr(name)}</text>`;
      if (tier >= 2) {
        labels += `<text class="wt-pie-pct" x="${lx.toFixed(1)}" y="${(ly + 7).toFixed(1)}"
                   text-anchor="middle" dominant-baseline="middle">${pcts[i]}%</text>`;
      }
    }
    a = a1;
  });

  const legend = subs.map((name, i) => `<li>
      <i style="background:${subjectColor(name)}"></i>
      <span class="wt-pie-name">${escapeAttr(name)}</span>
      <b>${mins[i]} 分</b>
      <em>${pcts[i]}%</em>
    </li>`).join('');

  return el(`<div class="wt-pie-body">
    <svg class="wt-pie-chart" viewBox="0 0 190 190" role="img"
         aria-label="${escapeAttr(ariaLabel)}">${slices}${labels}</svg>
    <ul class="wt-pie-legend">${legend}</ul>
  </div>`);
}

// 预计 / 实际 两张饼并排。放在柱状图正下方: 柱状图回答"哪天写了多久",
// 饼图回答"时间都花在哪个科目上", 两张图连着看才是完整的用时画像。
function renderSubjectShare(r) {
  // subject_share 缺失时按空处理, 下面会因合计为 0 而整块不画 —— 不能
  // 在这里返回 el(''): 那是 null, appendChild(null) 会在页面上留下一个
  // 字面量 "null"。
  const share = r.subject_share || {};
  const est = share.est || { by_subject: {}, total_minutes: 0 };
  const act = share.actual || { by_subject: {}, total_minutes: 0 };
  const estTotal = est.total_minutes || 0;
  const actTotal = act.total_minutes || 0;
  // 整段零用时: 整块不画。调用方据此跳过, 页面里只留那一处空态。
  if (!estTotal && !actTotal) return null;
  // "整周" / "整月" —— 统计口径的名字要跟着周期走。
  //
  // 饼图统计的是**整个区间** (后端算好的 subject_share), 不跟着柱状图
  // 一起藏周末: 周末的用时是真花掉的, 抹掉就等于少算了家长的付出。
  // 两者对得上账的前提是周末本来就没作业 —— 实测确实如此 (作业都在上学
  // 最后一天布置), 所以隐藏周末不会让饼图比柱子多出时间。
  const scope = r.period === 'month' ? '整月' : '整周';

  // 实际与预估的差额。一句话说明两个环差在哪, 不用家长自己对表。
  let delta = '';
  if (estTotal > 0 && actTotal > 0) {
    const diff = actTotal - estTotal;
    delta = diff === 0
      ? `实际与预估一致, 合计 ${actTotal} 分钟`
      : `实际比预估${diff > 0 ? '多' : '少'} ${Math.abs(diff)} 分钟 `
        + `(预估 ${estTotal} · 实际 ${actTotal})`;
  }

  const card = el(`<div class="card">
    <div class="card-title">各科时间占比<span class="count">${scope}</span></div>
    ${delta ? `<div class="muted wt-share-delta">${delta}</div>` : ''}
    <div class="wt-pies"></div>
  </div>`);

  const row = card.querySelector('.wt-pies');
  const estCol = el(`<div class="wt-pie">
    <div class="wt-pie-head">预计时间占比</div>
    <div class="wt-pie-total">合计 ${estTotal} 分钟</div>
  </div>`);
  estCol.appendChild(renderPie(est.by_subject, estTotal, '各科预计时间占比饼图'));
  const actCol = el(`<div class="wt-pie">
    <div class="wt-pie-head">实际时间占比</div>
    <div class="wt-pie-total">合计 ${actTotal} 分钟</div>
  </div>`);
  actCol.appendChild(renderPie(act.by_subject, actTotal, '各科实际时间占比饼图'));
  row.appendChild(estCol);
  row.appendChild(actCol);

  card.appendChild(el(`<div class="muted wt-scope">口径: 已完成作业 (跳过不计), 未填实际用时按预估计入; 选做计入, 观察作业不计; 统计区间为${scope}。</div>`));
  return card;
}

// 平日 / 周五 / 周末 / 调休 四档对照。这四档的作业量不是同一回事,
// 混在一起平均会得到一个不存在的数字, 所以并排摆出来让家长自己比。
//
// withWeekend=false 时不画「周末」那张卡: 周末那档的量几乎恒为 0 (作业
// 都是上学最后一天布置的), 摆一张 0 分钟的卡在家长眼前只会让人以为
// 数据漏了。「长档」= 周五 + 周末合计, 它的分母含周末, 所以周末不展示
// 时长档也一并去掉 —— 留着一张混了看不见的日子算出来的卡同样说不清。
function renderBuckets(r, withWeekend) {
  const pa = r.plain_avg || { days: 0, total_minutes: 0, by_subject: {} };
  const subs = orderedSubjects(pa.by_subject);
  const isMonth = r.period === 'month';

  // 周五与周末合成"长档": 周五通常已经把周末的量布置下去了,
  // 分开看会低估周末负担, 合并才是真实的"周末档"。
  const weekendTotal = (r.weekend?.total_minutes || 0);
  const fridayTotal = (r.friday?.total_minutes || 0);
  const longTotal = fridayTotal + weekendTotal;
  const longDays = (r.friday?.days || 0) + (r.weekend?.days || 0);

  // 每档备齐 天数 / 合计 / 日均 三个数, 两种视图只是挑不同的数当主角:
  //   周视图: 合计当主角 —— 这一周各档一共花了多少, 直接可比。
  //           周五/周末的日均没意义 (就一两天), 所以 avg 留空, 不画量表。
  //   月视图: 日均当主角 —— 各档天数差得远 (平日二十来天 vs 周五四五天),
  //           拿合计横向比等于在比"哪档天多"而不是"哪档更重"。
  //           合计退到小字里, 想看总量时也在。
  const buckets = [
    { title: '平日', sub: '周一至周四', days: pa.days,
      total: Math.round((pa.total_minutes || 0) * (pa.days || 0)), avg: pa.total_minutes, cls: 'is-plain' },
    { title: '周五', sub: '通常含周末作业量', days: r.friday?.days || 0,
      total: fridayTotal, avg: isMonth ? (r.friday?.avg_minutes ?? null) : null, cls: 'is-friday' },
  ];
  // 周末那两档只在"显示周末"时出现 —— 「长档」的分母含周末, 周末不画
  // 时它也说不清, 一起去掉 (详见函数头的注释)。
  if (withWeekend) {
    buckets.push({ title: '周末', sub: '周六至周日', days: r.weekend?.days || 0,
      total: weekendTotal, avg: isMonth ? (r.weekend?.avg_minutes ?? null) : null, cls: 'is-off' });
    buckets.push({ title: '长档', sub: '周五 + 周末合计', days: longDays, total: longTotal,
      avg: longDays ? Math.round(longTotal / longDays) : 0, cls: 'is-long' });
  }
  if ((r.makeup?.days || 0) > 0) {
    buckets.push({ title: '调休', sub: '周末补班, 要上学', days: r.makeup.days,
      total: r.makeup.total_minutes,
      avg: isMonth ? (r.makeup.avg_minutes ?? null) : null, cls: 'is-makeup' });
  }

  const card = (b) => {
    const bar = b.avg > 0 && pa.total_minutes > 0
      ? `<div class="wt-meter"><span style="width:${Math.min(100, b.avg / pa.total_minutes * 100).toFixed(1)}%"></span></div>`
      : '';
    const chips = subs.length && Object.keys(b.avg || {}).length
      ? `<div class="wt-chips">${subs.map(s =>
          `<span class="wt-chip"><i style="background:${subjectColor(s)}"></i>${escapeAttr(s)} ${b.avg[s]}分</span>`).join('')}</div>`
      : '';
    const head = isMonth ? (b.avg ?? 0) : b.total;
    const note = isMonth
      ? `${b.days} 天 · 合计 ${b.total} 分`
      : (b.avg != null ? `日均 ${b.avg} 分 · ${b.days} 天` : `${b.days} 天`);
    return el(`<div class="wt-bucket ${b.cls}">
      <div class="wt-bucket-head">
        <div class="wt-bucket-title">${b.title}</div>
        <div class="wt-bucket-sub">${b.sub}</div>
      </div>
      <div class="wt-bucket-num"><strong>${head}</strong><span class="wt-unit">分钟</span></div>
      <div class="muted">${note}</div>
      ${bar}
      ${chips}
    </div>`);
  };

  const box = el(`<div class="card"><div class="card-title">分档对照<span class="count">${isMonth ? '同一个月内' : '同一周内'}</span></div>`
    + '<div class="wt-buckets"></div></div>');
  const row = box.querySelector('.wt-buckets');
  buckets.forEach(b => row.appendChild(card(b)));
  return box;
}

// 口径说明: 解释图上各档日子的颜色。周末不展示时, "休息"这一档根本
// 不会出现, 图例里留着它等于解释一个看不见的东西 —— 过滤掉。
function renderLegend(withWeekend) {
  const items = Object.entries(DAY_KIND_META)
    .filter(([k]) => withWeekend || k !== 'off')
    .map(([k, m]) => `<span class="wt-lg"><i class="wt-lg-dot k-${k}"></i>${m.label} — ${m.hint}</span>`)
    .join('');
  return el(`<div class="card wt-legend-box"><div class="muted wt-legend-title">口径说明</div><div class="wt-legend">${items}</div></div>`);
}

function renderDeviationTable(box, r) {
  // data-label / col-advice 只给窄屏卡片布局用(见 styles.css §26g):
  // 7 列里"建议"是一整段中文, 挤进 344px 后会被压成一列一个字,
  // 完全读不了。窄屏改成每行一张卡, 表头收起, 标签由 data-label 提供。
  // 宽屏不读这两个属性, 对桌面零影响。
  const tbl = el(`<table class="review-table">
    <thead><tr><th>科目</th><th>类型</th><th>样本</th><th>估</th><th>实</th><th>偏差</th><th>建议</th></tr></thead>
    <tbody></tbody>
  </table>`);
  const tbody = tbl.querySelector('tbody');
  r.by_type.forEach(b => {
    const diffCls = b.diff_minutes > 0 ? 'diff-pos' : 'diff-neg';
    tbody.appendChild(el(`<tr>
      <td data-label="科目"><span class="subject-tag ${b.subject}">${b.subject}</span></td>
      <td data-label="类型">${b.task_type}</td>
      <td data-label="样本">${b.sample_size}</td>
      <td data-label="估">${b.avg_est_minutes}</td>
      <td data-label="实">${b.avg_actual_minutes}</td>
      <td data-label="偏差" class="${diffCls}">${b.diff_minutes > 0 ? '+' : ''}${b.diff_minutes}分 (${b.diff_pct}%)</td>
      <td data-label="建议" class="col-advice"><small>${b.advice}</small></td>
    </tr>`));
  });
  box.appendChild(el(`<h3>估时偏差</h3>`));
  box.appendChild(tbl);
  if (r.top_overestimate.length) {
    box.appendChild(el(`<h3>高估 TOP (你估多了)</h3>`));
    r.top_overestimate.forEach(b => box.appendChild(el(`<div class="advice-card">${b.advice}</div>`)));
  }
  if (r.top_underestimate.length) {
    box.appendChild(el(`<h3>低估 TOP (你估少了)</h3>`));
    r.top_underestimate.forEach(b => box.appendChild(el(`<div class="advice-card">${b.advice}</div>`)));
  }
}

// 一页回答三个问题: 这周哪天最满、有没有时间段撞车、固定节奏有没有
// 落到任务池里。它不是"再看一遍作业" —— 作业在「看板」, 这里只合成。

// 显示中的那一周以哪个日期为锚。null = 本周 (由后端按今天算)。
let _weekData = null;

function addDays(iso, n) {
  // 用本地时间构造, 不用 toISOString(): 后者是 UTC, 在东八区会退一天。
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() + n);
  const p = x => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function md(iso) {
  return `${parseInt(iso.slice(5, 7), 10)}/${parseInt(iso.slice(8, 10), 10)}`;
}

  const root = document.getElementById('week-content');
  if (!root) return;
  root.innerHTML = '<p class="muted">加载中…</p>';
  try {
    _weekData = w;
  } catch (e) {
    root.innerHTML = `<div class="error-banner">加载失败: ${escapeAttr(e.message)}</div>`;
  }
}

  root.innerHTML = '';

  const bar = el(`<div class="week-bar">
    <button type="button" class="ghost week-nav" data-step="-7">‹ 上周</button>
    <div class="week-range">
      <strong>${md(w.week_start)} – ${md(w.week_end)}</strong>
      <span class="muted">每天上限 ${w.daily_load_limit} 分钟</span>
    </div>
    <button type="button" class="ghost week-nav" data-step="7">下周 ›</button>
  </div>`);
  bar.querySelectorAll('.week-nav').forEach(b => b.addEventListener('click', () => {
  }));
  root.appendChild(bar);

  const t = w.totals;
  root.appendChild(el(`<div class="card week-summary">
    <div class="card-title">本周负荷<span class="count">${md(w.week_start)} ~ ${md(w.week_end)}</span></div>
    <div class="week-stats">
      <div class="week-stat"><strong>${t.load_minutes}</strong><span>总负荷(分)</span></div>
      <div class="week-stat ${t.overloaded_days ? 'warn' : ''}"><strong>${t.overloaded_days}</strong><span>超载天数</span></div>
      <div class="week-stat ${t.conflict_days ? 'warn' : ''}"><strong>${t.conflict_days}</strong><span>冲突天数</span></div>
    </div>
  </div>`));

  const actions = el(`<div class="week-actions">
    <button type="button" class="primary" id="week-generate">生成本周任务</button>
    <button type="button" class="secondary" id="week-manage-slots">管理课表</button>
    <button type="button" class="secondary" id="week-manage-plans">周计划项</button>
  </div>`);
  root.appendChild(actions);

  const days = el('<div class="week-days"></div>');
  root.appendChild(days);
}

  const card = el(`<div class="card week-day ${d.is_today ? 'is-today' : ''} ${d.overloaded ? 'overloaded' : ''}">
    <div class="week-day-head">
      <span class="week-day-name">${d.label}</span>
      <span class="week-day-date">${md(d.date)}</span>
      ${d.is_today ? '<span class="week-today-tag">今天</span>' : ''}
      <span class="week-day-load">${d.load_minutes} 分</span>
      ${d.overloaded ? '<span class="week-badge warn">超载</span>' : ''}
      ${d.conflicts.length ? '<span class="week-badge danger">冲突</span>' : ''}
    </div>`);

  if (d.slots.length) {
    const chips = el('<div class="week-chips"></div>');
    d.slots.forEach(s => chips.appendChild(el(
      `<span class="week-slot"><strong>${escapeAttr(s.project_name)}</strong> ${s.start_time}-${s.end_time}${s.note ? ' · ' + escapeAttr(s.note) : ''}</span>`
    )));
    card.appendChild(chips);
  }
  if (d.plans.length) {
    const chips = el('<div class="week-chips"></div>');
    d.plans.forEach(p => chips.appendChild(el(
    )));
    card.appendChild(chips);
  }
  const a = d.assignments;
  const stats = a.total
    ? `作业 ${a.total} 项 · 完成 ${a.done} · 剩 ${a.remaining_minutes} 分`
    : '当天还没有任务';
  d.conflicts.forEach(c => card.appendChild(el(`<div class="week-conflict">⚠ ${escapeAttr(c.message)}</div>`)));
  return card;
}

  const btn = document.getElementById('week-generate');
  if (btn) btn.disabled = true;
  try {
    if (r.created_count) {
      toast(`已生成 ${r.created_count} 项任务${r.skipped_count ? `, 跳过 ${r.skipped_count} 项重复` : ''}`);
    } else if (r.skipped_count) {
      toast('这些任务都已经在任务池里了');
    } else {
      toast('还没有周计划项, 先点「周计划项」添加');
    }
  } catch (e) {
    toast('生成失败: ' + e.message);
    if (btn) btn.disabled = false;
  }
}

// ---- 课表时段管理 ----
  const w = _weekData;
  if (!w) return;
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = '固定课表';
  const content = document.getElementById('modal-content');
  content.innerHTML = '<div class="manage-list"></div>' +
    '<div class="row"><button type="button" class="secondary" id="slot-add">+ 新增上课时段</button></div>';
  m.classList.remove('hidden');
  m.classList.remove('confirm-modal');
  setModalActions({ mode: 'close' });

  const list = content.querySelector('.manage-list');
  if (!w.slots.length) {
    list.appendChild(el('<p class="muted">还没有固定课表。加上「钢琴周三 16:30-18:00」后, 周计划会把它算进当天负荷。</p>'));
  }
  w.slots.forEach(s => {
    const row = el(`<div class="manage-row">
      <div class="manage-main">
        <strong>${escapeAttr(s.project_name)}</strong>
      </div>
      <div class="manage-ops">
        <button type="button" class="secondary" data-op="edit">改</button>
        <button type="button" class="danger" data-op="del">删</button>
      </div>
    </div>`);
    row.querySelector('[data-op="del"]').addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: '删除课表时段',
        lead: `删除「${s.project_name} ${WEEKDAY_LABELS[s.weekday]} ${s.start_time}-${s.end_time}」?`,
        okText: '删除',
        danger: true,
      });
      try {
        await api('DELETE', `/schedule-slots/${s.id}`);
        toast('已删除课表时段');
      } catch (e) {
        toast('删除失败: ' + e.message);
      }
    });
    list.appendChild(row);
  });
}

  if (!_qaProjects.length) await refreshQuickAddData();
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = slot ? '改上课时段' : '新增上课时段';
  const content = document.getElementById('modal-content');
  const weekday = slot ? slot.weekday : 2;
  content.innerHTML = `<div class="form-grid">
    <label>
      <span class="form-label">项目</span>
      ${projectPickerHtml('slot-project', slot && slot.project_id, slot && slot.project_name)}
    </label>
    <label>
      <span class="form-label">星期</span>
      <select id="slot-weekday">
      </select>
    </label>
    <label>
      <span class="form-label">开始</span>
      <input id="slot-start" type="time" value="${slot ? slot.start_time : '16:30'}" />
    </label>
    <label>
      <span class="form-label">结束</span>
      <input id="slot-end" type="time" value="${slot ? slot.end_time : '18:00'}" />
    </label>
    <label>
      <span class="form-label">备注</span>
      <span class="form-field">
        <input id="slot-note" maxlength="60" value="${slot && slot.note ? escapeAttr(slot.note) : ''}" />
        <span class="form-hint">如"琴房 3 号", 可不填</span>
      </span>
    </label>
  </div>`;
  initProjectPickers(content);
  m.classList.remove('hidden');
  m.classList.remove('confirm-modal');
  setModalActions({
    mode: 'confirm',
    okText: slot ? '保存' : '新增',
    // 这个弹窗是「固定课表」的下层 (从列表点「＋新增上课时段」进来的),
    // 次要键是"返回上一层" —— 回到课表列表, 而不是把整个弹窗关掉。
    // 上一层还在, 按一下回去比一键退出更顺手; 列表层自己的「返回」才是
    // 退出到周计划页。
    onOk: async () => {
      const pid = parseInt(document.getElementById('slot-project').value, 10);
      if (!Number.isFinite(pid)) { toast('先选一个项目'); return; }
      const start = document.getElementById('slot-start').value;
      const end = document.getElementById('slot-end').value;
      if (!start || !end) { toast('开始与结束时间都要填'); return; }
      if (end <= start) { toast('结束时间必须晚于开始时间'); return; }
      const body = {
        project_id: pid,
        weekday: parseInt(document.getElementById('slot-weekday').value, 10),
        start_time: start,
        end_time: end,
        note: document.getElementById('slot-note').value.trim() || null,
      };
      const okBtn = document.getElementById('modal-ok');
      okBtn.disabled = true;
      try {
        if (slot) await api('PATCH', `/schedule-slots/${slot.id}`, body);
        else await api('POST', '/schedule-slots', body);
        toast(slot ? '已保存课表时段' : '已新增课表时段');
        // 存完回到课表列表这一层: 新增的那条要看得见, 而不是把人扔回
        // 周计划页再让他重新点一遍「管理课表」。
      } catch (e) {
        okBtn.disabled = false;
        toast('保存失败: ' + e.message);   // 留在弹窗里, 冲突提示要能看到
      }
    },
  });
}

// ---- 周计划项管理 ----
  const w = _weekData;
  if (!w) return;
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = '周计划项';
  const content = document.getElementById('modal-content');
  content.innerHTML = '<div class="manage-list"></div>' +
    '<div class="row"><button type="button" class="secondary" id="plan-add">+ 新增计划项</button></div>' +
    '<p class="muted">计划项是每周固定的任务模板; 点「生成本周任务」后才会变成看板上的作业。</p>';
  m.classList.remove('hidden');
  m.classList.remove('confirm-modal');
  setModalActions({ mode: 'close' });

  const list = content.querySelector('.manage-list');
    list.appendChild(el('<p class="muted">还没有周计划项。例如「每日阅读 20 分钟」勾周一至周五。</p>'));
  }
    const label = p.project_name || p.subject || TASK_KIND_LABELS[p.task_kind] || '任务';
    const row = el(`<div class="manage-row">
      <div class="manage-main">
        <strong>${escapeAttr(p.task_name)}</strong>
        <span class="manage-meta">${escapeAttr(label)} · 周${days} · ${p.est_minutes} 分</span>
      </div>
      <div class="manage-ops">
        <button type="button" class="secondary" data-op="edit">改</button>
        <button type="button" class="danger" data-op="del">删</button>
      </div>
    </div>`);
    row.querySelector('[data-op="del"]').addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: '删除周计划项',
        lead: `删除「${p.task_name}」?`,
        note: '已经生成到看板的作业不受影响。',
        okText: '删除',
        danger: true,
      });
      try {
        await api('DELETE', `/plan-items/${p.id}`);
        toast('已删除计划项');
      } catch (e) {
        toast('删除失败: ' + e.message);
      }
    });
    list.appendChild(row);
  });
}

  if (!_qaProjects.length) await refreshQuickAddData();
  const kind = item ? item.task_kind : 'subject';
  const isSubject = kind === 'subject';
  const days = item ? item.weekdays : [0, 1, 2, 3, 4];
  const m = document.getElementById('modal');
  document.getElementById('modal-title').textContent = item ? '改周计划项' : '新增周计划项';
  const content = document.getElementById('modal-content');
  content.innerHTML = `<div class="form-grid">
    <label>
      <span class="form-label">任务来源</span>
      <select id="pi-kind">
        ${[['subject', '学科作业'], ['training', '培训'], ['habit', '习惯']]
          .map(([k, label]) => `<option value="${k}" ${k === kind ? 'selected' : ''}>${label}</option>`).join('')}
      </select>
    </label>
    <label id="pi-subject-wrap" ${isSubject ? '' : 'hidden'}>
      <span class="form-label">科目</span>
      <select id="pi-subject">
        ${['语文', '数学', '英语'].map(s => `<option ${item && s === item.subject ? 'selected' : ''}>${s}</option>`).join('')}
      </select>
    </label>
    <label id="pi-project-wrap" ${isSubject ? 'hidden' : ''}>
      <span class="form-label">项目</span>
      ${projectPickerHtml('pi-project', item && item.project_id, item && item.project_name)}
    </label>
    <label>
      <span class="form-label">任务名</span>
      <input id="pi-name" maxlength="200" value="${item ? escapeAttr(item.task_name) : ''}" placeholder="如 每日阅读" />
    </label>
    <label>
      <span class="form-label">预计用时</span>
      <input id="pi-est" type="number" min="1" max="180" inputmode="numeric" value="${item ? item.est_minutes : 15}" />
    </label>
    <label>
      <span class="form-label">星期</span>
      <span class="form-field">
        <span class="weekday-checks">
            `<label class="weekday-check"><input type="checkbox" value="${i}" ${days.includes(i) ? 'checked' : ''}/>${label.slice(1)}</label>`).join('')}
        </span>
        <span class="form-hint">勾选每周固定做的日子</span>
      </span>
    </label>
  </div>`;
  initProjectPickers(content);
  m.classList.remove('hidden');
  m.classList.remove('confirm-modal');
  document.getElementById('pi-kind').addEventListener('change', () => {
    const toSubject = document.getElementById('pi-kind').value === 'subject';
    document.getElementById('pi-subject-wrap').hidden = !toSubject;
    document.getElementById('pi-project-wrap').hidden = toSubject;
  });
  setModalActions({
    mode: 'confirm',
    okText: item ? '保存' : '新增',
    // 与上课时段同一个层级关系: 这个弹窗是「周计划项」列表的下层,
    // 次要键退回上一层 (列表), 列表层的「返回」才退出到周计划页。
    onOk: async () => {
      const name = document.getElementById('pi-name').value.trim();
      if (!name) { toast('任务名不能为空'); return; }
      const est = parseInt(document.getElementById('pi-est').value, 10);
      if (!(est >= 1 && est <= 180)) { toast('预计用时需在 1~180 分钟'); return; }
      const checkedDays = [...content.querySelectorAll('.weekday-checks input:checked')]
        .map(i => parseInt(i.value, 10));
      if (!checkedDays.length) { toast('至少选择一天'); return; }
      const kindVal = document.getElementById('pi-kind').value;
      const isSubj = kindVal === 'subject';
      const pid = parseInt(document.getElementById('pi-project').value, 10);
      const body = {
        task_kind: kindVal,
        subject: isSubj ? document.getElementById('pi-subject').value : null,
        project_id: isSubj || !Number.isFinite(pid) ? null : pid,
        task_name: name,
        est_minutes: est,
        weekdays: checkedDays.sort((a, b) => a - b),
      };
      const okBtn = document.getElementById('modal-ok');
      okBtn.disabled = true;
      try {
        if (item) await api('PATCH', `/plan-items/${item.id}`, body);
        else await api('POST', '/plan-items', body);
        toast(item ? '已保存计划项' : '已新增计划项');
        // 存完回到周计划项列表这一层, 与课表时段同构: 新加的那条要看得见,
        // 而不是把人直接扔回周计划页再让他重新点一遍「周计划项」。
      } catch (e) {
        okBtn.disabled = false;
        toast('保存失败: ' + e.message);
      }
    },
  });
}

// ====== 设置 ======
// 配置键名对家长毫无意义 (school_dismiss_time 读不出是"放学时间"),
// 这里给常见项配中文名。原始键仍以小字 code 显示在下方, 便于对照与排错。
const SETTING_LABELS = {
  ai_model_version: 'AI 模型版本',
  dinner_time: '晚餐时间',
  grade: '年级',
  school_dismiss_time: '放学时间',
  subject_baseline_minutes: '各科预估用时（分钟）',
  task_type_baseline_minutes: '各作业类型预估用时（分钟）',
};

async function loadSettings() {
  const root = document.getElementById('settings-content');
  root.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const r = await api('GET', '/settings');
    const wrap = el('<div class="settings-list"></div>');
    r.items.forEach(it => {
      // 短值走紧凑的"标签 + 输入 + 保存"同一行布局(见 .setting-row);
      // JSON 字典内容多, 仍用整块的键值表。
      const card = el(`<div class="card setting-card">
        <label class="setting-label" for="set-${escapeAttr(it.key)}">
          <span class="setting-name">${escapeAttr(SETTING_LABELS[it.key] || it.key)}</span>
          ${SETTING_LABELS[it.key] ? `<code class="setting-key">${escapeAttr(it.key)}</code>` : ''}
        </label>
      </div>`);

      // 尝试把 value 解析为 JSON object; 成功 → 编辑键值表, 失败 → 文本框兜底
      // 修 P1: 之前 escapeAttr(it.value) 直接显示 \uXXXX, 家长看不懂中文
      let parsed = null;
      let parseOk = false;
      try {
        parsed = JSON.parse(it.value);
        parseOk = parsed != null && typeof parsed === 'object' && !Array.isArray(parsed);
      } catch (e) {
        parseOk = false;
      }

      if (parseOk) {
        // JSON object → 键值编辑表 (替代原文本框)
        // 加 is-block 类让卡片转为纵向整块排布。不用 :has() 兜底 ——
        // 那依赖父级选择器, 老浏览器会整条规则失效, 布局就散掉了。
        card.classList.add('is-block');
        const kvWrap = el('<div class="settings-kv"></div>');
        const tbl = el('<table class="settings-kv-table"><thead><tr><th>键</th><th>值</th><th></th></tr></thead><tbody></tbody></table>');
        const tbody = tbl.querySelector('tbody');

        function renderKvRow(k, v) {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td><input data-kv="k" value="${escapeAttr(String(k))}" /></td>
            <td><input data-kv="v" value="${escapeAttr(String(v))}" /></td>
            <td><button class="ghost kv-del" type="button">删</button></td>
          `;
          tr.querySelector('.kv-del').addEventListener('click', () => tr.remove());
          tbody.appendChild(tr);
        }

        Object.entries(parsed).forEach(([k, v]) => renderKvRow(k, v));
        kvWrap.appendChild(tbl);

        const addBtn = el('<button class="ghost" type="button">+ 添加一行</button>');
        addBtn.addEventListener('click', () => renderKvRow('', ''));
        kvWrap.appendChild(addBtn);

        const saveBtn = el('<button class="primary save-btn" type="button">保存</button>');
        saveBtn.addEventListener('click', async () => {
          const newObj = {};
          tbody.querySelectorAll('tr').forEach(tr => {
            const k = tr.querySelector('[data-kv="k"]').value.trim();
            const vRaw = tr.querySelector('[data-kv="v"]').value;
            if (!k) return;  // 跳过空键行
            // 智能推断类型: null/true/false/数字/字符串 (原 JSON 值类型尽量保留)
            let v;
            if (vRaw === 'null') v = null;
            else if (vRaw === 'true') v = true;
            else if (vRaw === 'false') v = false;
            else if (vRaw !== '' && !isNaN(Number(vRaw))) v = Number(vRaw);
            else v = vRaw;
            newObj[k] = v;
          });
          try {
            // 修真修真修真: JSON.stringify(parsed) 重新序列化, 始终 ensure_ascii=False
            await api('PATCH', `/settings/${it.key}`, { value: JSON.stringify(newObj) });
            // 同步刷新解析页的兜底缓存, 否则家长改完配置,
            // 下一次解析仍会按旧值填那些 AI 没给时长的行.
            loadBaselines();
            toast('已保存');
          } catch (e) { toast('保存失败: ' + e.message); }
        });
        kvWrap.appendChild(saveBtn);
        card.appendChild(kvWrap);
      } else {
        // 非 JSON object (字符串/数字/数组/解析失败) → 按内容选控件并紧凑排列。
        //
        // 之前一律用 rows="3" 的 textarea 竖着堆, 但实际值里
        // "ai_model_version" / "dinner_time" / "grade" /
        // "school_dismiss_time" 全是单行短值 (最长 9 字符),
        // 却各占 120px 高, 四个设置项就撑满两屏还多。
        // 改为: 短值用单行 input 与标签同行, 长文本才用 textarea。
        const isMultiline = /\r?\n/.test(it.value);
        const isShort = !isMultiline && it.value.length <= 60;

        const row = el('<div class="setting-row"></div>');
        const field = isShort
          ? el(`<input type="text" id="set-${escapeAttr(it.key)}" class="setting-input" data-key="${it.key}" value="${escapeAttr(it.value)}" />`)
          : el(`<textarea id="set-${escapeAttr(it.key)}" class="setting-input setting-textarea" data-key="${it.key}" rows="3">${escapeAttr(it.value)}</textarea>`);
        row.appendChild(field);

        const saveBtn = el('<button class="primary save-btn" type="button">保存</button>');
        row.appendChild(saveBtn);
        card.appendChild(row);

        saveBtn.addEventListener('click', async () => {
          try {
            await api('PATCH', `/settings/${it.key}`, { value: field.value });
            toast('已保存');
          } catch (e) { toast('保存失败: ' + e.message); }
        });
      }

      wrap.appendChild(card);
    });
    root.innerHTML = '';
    root.appendChild(wrap);
    await renderProjectsBlock(root);
  } catch (e) {
    root.innerHTML = `<div class="error-banner">加载失败: ${e.message}</div>`;
  }
}

// ====== 设置页: 项目管理 (M1) ======
// 培训/习惯任务的归类靠项目。这里只做三件事: 看列表 / 新建 / 归档。
// 归档而非删除: 历史作业仍引用 project_id, 删掉会让"这周练了几次琴"
// 的统计凭空消失。归档后只是不再出现在录入候选里。
async function renderProjectsBlock(root) {
  let items = [];
  try {
    items = (await api('GET', '/projects', undefined)).items || [];
  } catch (e) {
    return;   // 项目接口失败不该让整页设置跟着报错
  }
  const card = el(`<div class="card project-manage">
    <div class="card-title">项目 / 培训班<span class="count">${items.length}</span></div>
    <div class="proj-list"></div>
    <div class="row proj-new">
      <input id="proj-new-name" placeholder="项目名, 如 钢琴" aria-label="项目名" />
      <button id="proj-add" class="ghost" type="button">新建项目</button>
    </div>
    <p class="muted">项目用于归类培训与习惯任务, 归档后历史记录仍保留</p>
  </div>`);
  const list = card.querySelector('.proj-list');
  if (!items.length) {
    list.appendChild(el('<p class="muted">还没有项目。可以在录入页用快捷模板一键创建。</p>'));
  }
  items.forEach(p => {
    const row = el(`<div class="proj-row">
      <span class="proj-name">${escapeAttr(p.name)}</span>
      <span class="proj-meta">${escapeAttr(p.project_type)}${p.coach_name ? ' · ' + escapeAttr(p.coach_name) : ''}${p.schedule_note ? ' · ' + escapeAttr(p.schedule_note) : ''}</span>
      <button class="ghost proj-archive" type="button">归档</button>
    </div>`);
    row.querySelector('.proj-archive').addEventListener('click', async () => {
      // 归档是"可恢复但会改变候选列表"的操作, 走自绘确认弹窗,
      // 与删除作业同一套语言 (项目规则: 不用浏览器原生 confirm)。
      const ok = await confirmDialog({
        title: '归档项目',
        lead: `归档「${p.name}」?`,
        note: '历史记录会保留, 之后录入时不再出现在候选里。',
        okText: '归档',
        danger: true,
      });
      if (!ok) return;
      try {
        await api('DELETE', `/projects/${p.id}`);
        await refreshQuickAddData();
        loadSettings();
        toast(`已归档「${p.name}」`);
      } catch (e) { toast('归档失败: ' + e.message); }
    });
    list.appendChild(row);
  });
  card.querySelector('#proj-add').addEventListener('click', async () => {
    const input = card.querySelector('#proj-new-name');
    const name = input.value.trim();
    if (!name) { toast('先填项目名'); return; }
    try {
      const p = await api('POST', '/projects', { name });
      input.value = '';
      await refreshQuickAddData();
      loadSettings();
      toast(`已创建项目「${p.name}」`);
    } catch (e) { toast('创建失败: ' + e.message); }
  });
  root.appendChild(card);
}

document.getElementById('btn-export').addEventListener('click', () => {
  window.location.href = API + '/export';
});

// ====== 启动 ======
loadHealth();
// 直接打开 /#/settings 时必须先按 hash 切好页, 否则会先渲染看板再切,
// 家长会看到一次无意义的看板请求和一闪而过的内容。
switchTab(tabFromHash(), { updateUrl: false });
setInterval(loadHealth, 60000);