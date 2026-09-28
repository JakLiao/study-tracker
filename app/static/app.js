// ====== study-tracker 前端 (视觉对齐版 · 2026-09-28) ======
// 配套 docs/02-页面设计.md §9 "给码灵实现的 4 个前端坑"
// 1. 数字 tabular-nums (在 styles.css 全局声明)
// 2. pending_actual 中间态: done 后未填实耗, 输入框高亮 + Toast 兜底
// 3. 低置信度任务显眼: 整行浅红底 + 暗红边条 + ⚠ 徽章
// 4. "手动添加" 按钮始终可见, AI 不可用时加暗红描边
'use strict';

const API = '/api';
const MAX_TEXT_LEN = 10000;

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

// ====== Tab 切换 ======
function switchTab(name) {
  document.querySelectorAll('#tabs button').forEach(b => {
    const active = b.dataset.tab === name;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  if (name === 'board') {
    // 回到看板时重置到最新一天, 否则家长会停在几天前的那天上,
    // 以为"今天的作业怎么不见了"。
    _boardDate = null;
    _boardScroll = 0;
    loadBoard();
  }
  // 日历与详情一起加载。保留 _calYear/_calMonth, 切走再切回时
  // 家长仍停在上次翻到的那个月, 而不是被强行弹回本月。
  if (name === 'list') {
    loadCalendar();
    if (_calSelected) loadList(_calSelected);
  }
  if (name === 'weekly') loadWeekly('current');
  if (name === 'settings') loadSettings();
}
document.querySelectorAll('#tabs button').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});
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
      const b = el(`
        <button class="date-tab ${d.date === active ? 'active' : ''}" data-date="${d.date}">
          <span class="dt-label">${d.label}</span>
          <span class="dt-count">${d.done}/${d.total}</span>
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

    // 假期提示: 最新有作业的日期不是今天, 说明这几天是假期/无作业。
    // 否则家长会困惑"明明今天有课, 怎么一条作业都没有"。
    if (dates[0].date !== todayStr()) {
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
    await loadBoardDateTabs();
    const b = await api('GET', _boardDate ? `/board?date=${_boardDate}` : '/board/today');
    // 首次进入时锁定到后端返回的实际日期, 避免重复请求同一天
    if (!_boardDate) _boardDate = b.date;
    root.innerHTML = '';
    if (b.is_late_night) {
      root.appendChild(el('<p class="muted">已是凌晨, 注意休息</p>'));
    }
    if (!b.has_data) {
      // 空状态 (设计文档 §7.6)
      root.appendChild(el(`
        <div class="empty">
          <div class="empty-icon">📋</div>
          <p>${b.is_today ? '今天还没有作业' : '这天没有作业记录'}</p>
          ${b.is_today ? '<button class="primary" data-action="goto-entry">去录入</button>' : ''}
          <p class="muted">${b.is_today ? '或者手动添加一条作业' : '可以在「日历」页查看其他日期'}</p>
        </div>
      `));
      const go = root.querySelector('[data-action="goto-entry"]');
      if (go) go.addEventListener('click', () => switchTab('entry'));
      return;
    }
    // 进度条 (2026-09-28 口径: 跳过算已收尾, 分母排除选做)
    const p = b.progress;
    const pct = p.pct != null ? p.pct : 0;
    const skippedNote = p.skipped ? ` · 跳过 ${p.skipped}` : '';
    const optNote = p.optional ? ` · 选做 ${p.optional}` : '';
    root.appendChild(el(`
      <div class="progress-bar">
        <div class="progress-text">
          <span>${b.label === '今天' ? '' : b.label + ' · '}已收尾 <strong>${p.settled}</strong> / 必做 <strong>${p.required}</strong> · ${pct}%</span>
          <span class="muted">完成 ${p.done}${skippedNote}${optNote} · 估时 ${p.est_total || 0} 分</span>
        </div>
        <div class="progress-track"><div class="progress-fill" style="width:${pct}%;"></div></div>
      </div>
    `));
    // 截止时间提醒 (设计文档 §7.1 第 2 块)
    if (b.upcoming_deadlines && b.upcoming_deadlines.length) {
      const items = b.upcoming_deadlines.map(d =>
        `<span class="subject-tag ${d.subject}">${d.subject}</span>
         ${d.task_name} · 截止 ${fmtDateTime(d.deadline_time)}`
      ).join('<br/>');
      root.appendChild(el(`<div class="deadline-warn"><strong>⚠ 未来 7 天到期</strong>${items}</div>`));
    }

    // 分区: 待完成 / 已收尾 / 选做(折叠)
    // 口径说明: done 与 skipped 都算"已收尾", 但只有 done 算"完成"
    const all = [];
    for (const subj of ['语文', '数学', '英语']) all.push(...(b.by_subject[subj] || []));
    const isSettled = i => i.status === 'done' || i.status === 'skipped';
    const pending = all.filter(i => !isSettled(i));
    const settled = all.filter(i => isSettled(i));

    if (pending.length) {
      root.appendChild(el(`<div class="board-hint">今日还有 <strong>${pending.length}</strong> 项要做</div>`));
    } else if (all.length) {
      root.appendChild(el('<div class="board-hint all-clear">今日必做已全部收尾 ✓</div>'));
    }

    const section = (title, items, cls) => {
      if (!items.length) return;
      const doneN = items.filter(i => i.status === 'done').length;
      const card = el(`<div class="card ${cls || ''}"><div class="card-title">${title}<span class="count">${doneN}/${items.length}</span></div><div class="card-body"></div></div>`);
      items.forEach(item => card.querySelector('.card-body').appendChild(renderAssignment(item, true)));
      root.appendChild(card);
    };
    section('待完成', pending, 'sec-pending');
    section('已收尾', settled, 'sec-settled');

    // 选做分组 (默认收起 —— 低优先级内容不该稀释主任务视觉权重)
    if (b.optional_items && b.optional_items.length) {
      const opt = b.optional_items;
      const optDone = opt.filter(i => i.status === 'done').length;
      const card = el(`<details class="card optional-card"><summary class="card-title">选做 (${opt.length})<span class="count">${optDone}/${opt.length}</span></summary><div class="card-body"></div></details>`);
      opt.forEach(item => card.querySelector('.card-body').appendChild(renderAssignment(item, true)));
      root.appendChild(card);
    }
  } catch (e) {
    root.innerHTML = `<div class="error-banner">看板加载失败: ${e.message}</div>`;
  }
  if (scroller) scroller.scrollTop = _boardScroll;
}

// 渲染任务行 (设计文档 §5.1 + §9 4 个坑)
// onChange: 状态变更后的回调。看板传 loadBoard, 历史日历传刷新日历+详情,
// 不传则按钮不重绘 —— 历史页若复用 loadBoard 会把用户弹出日历页。
function renderAssignment(item, withStatus, onChange) {
  // pending_actual: done 但未填实耗时 (设计文档 §9 坑 2)
  const isPendingActual = item.status === 'done' && item.actual_minutes == null;
  const confClass = `confidence-${item.confidence}`; // 给整行加边条 / 底色
  // 选做行加暖色底 + 左侧竖线, 与必做行区分 (2026-09-28)
  const optClass = item.is_optional ? 'is-optional' : '';
  const row = el(`
    <div class="assignment-row ${item.status} ${confClass} ${optClass} ${isPendingActual ? 'pending-actual' : ''}" data-id="${item.id}">
      <div class="row-main">
        <span class="subject-tag ${item.subject}">${item.subject}</span>
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
        toast('已标记完成, 建议补填实际用时 (不填不影响, 但周复盘不计)');
      } else if (s === 'skipped') {
        toast('已跳过, 不计入完成数');
      }
      await refresh();
    } catch (e) {
      btn.disabled = false;
      toast('状态更新失败: ' + e.message);      // 用 toast 而非 alert, 不打断流程
    }
  };

  // 完成: todo/doing -> done
  if (item.status === 'todo' || item.status === 'doing') {
    const b = el(`<button class="status-btn done" aria-label="标记为已完成">完成</button>`);
    b.addEventListener('click', () => patchStatus('done', b));
    actions.appendChild(b);
  } else if (item.status === 'done') {
    // 重做: done -> todo。
    // 点错一次就必须能改, 否则家长只能去数据库里手改。
    // 注意后端在 done -> 非 done 时会清空 actual_minutes,
    // 所以这项会退回"待补实际用时"状态, 属预期行为。
    const b = el(`<button class="status-btn redo" aria-label="撤销完成, 重新标记为待完成">重做</button>`);
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
  if (isPendingActual) {
    const inputWrap = el('<div class="row" style="margin:0;"></div>');
    const input = el(`<input type="number" class="actual-input" placeholder="填实际用时" min="0" max="240" style="width:90px;" inputmode="numeric" />`);
    const saveBtn = el('<button class="primary">保存用时</button>');
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
      input.placeholder = '不填则不计入周复盘';
      toast('本次不记录用时, 之后仍可补填');
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveBtn.click(); });
    inputWrap.append(input, saveBtn, skipBtn);
    actions.appendChild(inputWrap);
    // 进入视口自动 focus (设计文档 §9 坑 2)
    setTimeout(() => input.focus(), 50);
  } else if (item.actual_minutes != null) {
    // 已填, 给修改入口
    const editBtn = el('<button class="ghost">改用时</button>');
    editBtn.addEventListener('click', async () => {
      const v = prompt('改成实际用时多少分钟 (0~240)', item.actual_minutes);
      if (v == null) return;
      const n = parseInt(v, 10);
      if (isNaN(n) || n < 0 || n > 240) { toast('用时需在 0~240 分钟之间'); return; }
      try {
        await api('PATCH', `/assignments/${item.id}/completion`, { actual_minutes: n });
        refresh();
      } catch (e) { toast('保存失败: ' + e.message); }
    });
    actions.appendChild(editBtn);
  }

  // 删除 —— 已完成的作业后端强制禁止删除 (4010), 所以这里直接不渲染,
  // 否则就是给用户一个"点了必然报错"的按钮。
  if (item.status !== 'done') {
    // 带 status-btn 让它跟随次操作档的小尺寸。删除是低频且不可逆的
    // 操作, 不该和"完成"抢视觉权重。
    const delBtn = el('<button class="status-btn danger">删除</button>');
    delBtn.addEventListener('click', async () => {
      if (!confirm(`确定删除 "${item.task_name}"?`)) return;
      try {
        await api('DELETE', `/assignments/${item.id}`);
        refresh();
      } catch (e) { toast('删除失败: ' + e.message); }
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
    ${item.est_minutes != null ? `<p>预估用时: ${item.est_minutes} 分钟</p>` : ''}
    ${item.actual_minutes != null ? `<p>实际用时: ${item.actual_minutes} 分钟</p>` : ''}
  `;
  // 原文弹窗是只读的, 取消与确定都只是关闭, 并排两个同功能按钮
  // 会让家长以为"确定"另有含义。这里只留一个「返回」。
  setModalActions({ mode: 'close' });
}

// 弹窗底部按钮按语义切换 (2026-09-28)。
//   close  —— 只读弹窗: 隐藏取消, 「确定」改文案为「返回」
//   confirm —— 输入弹窗: 保留「取消」+「确定」, 由调用方接确定按钮的 onclick
// 不能简单地删掉取消按钮: 手动添加弹窗里"取消"(放弃)与"确定"(提交)
// 是两种不同操作, 合并会丢掉"放弃这次编辑"的语义。
function setModalActions({ mode, onOk = null }) {
  const cancel = document.getElementById('modal-cancel');
  const ok = document.getElementById('modal-ok');
  if (mode === 'close') {
    cancel.classList.add('hidden');
    ok.textContent = '返回';
    ok.classList.remove('primary');
    ok.classList.add('ghost', 'modal-solo-btn');
    ok.onclick = () => document.getElementById('modal').classList.add('hidden');
  } else {
    cancel.classList.remove('hidden');
    ok.textContent = '确定';
    ok.classList.add('primary');
    ok.classList.remove('ghost', 'modal-solo-btn');
    ok.onclick = onOk;
  }
}

document.getElementById('modal-cancel').addEventListener('click', () => {
  document.getElementById('modal').classList.add('hidden');
});

// ====== 录入 / 解析 ======
document.getElementById('btn-parse').addEventListener('click', parseRaw);
document.getElementById('btn-add-manual').addEventListener('click', () => openManualAddModal());

// 字符计数 (设计文档 §7.2 第 1 块)
const rawTextarea = document.getElementById('raw-text');
rawTextarea.addEventListener('input', () => {
  const len = rawTextarea.value.length;
  const el2 = document.getElementById('char-count-num');
  el2.textContent = len;
  el2.parentElement.classList.toggle('warn', len > MAX_TEXT_LEN * 0.9);
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

function renderPreviewTable(items, batchId) {
  const result = document.getElementById('parse-result');
  result.innerHTML = `
    <h3>预览 / 编辑 (${items.length} 条)</h3>
    <p class="muted">低置信度行会高亮, 默认未勾选 (设计 §9 坑 3)</p>
    <table class="preview-table">
      <thead><tr>
        <th>勾</th><th>科目</th><th>任务名</th><th>归属日</th><th>截止</th>
        <th>选做</th><th>预估分</th><th>置信</th><th>原文</th><th></th>
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
    tr.innerHTML = `
      <td><input data-field="checked" type="checkbox" ${checkedDefault ? 'checked' : ''} /></td>
      <td>
        <select data-field="subject">
          <option ${it.subject==='语文'?'selected':''}>语文</option>
          <option ${it.subject==='数学'?'selected':''}>数学</option>
          <option ${it.subject==='英语'?'selected':''}>英语</option>
        </select>
      </td>
      <td><input data-field="task_name" value="${escapeAttr(it.task_name)}" /></td>
      <td>
        <input data-field="due_date" type="date" class="js-datepicker ${noAiDate ? 'need-date' : ''}"
               value="${it.due_date || ''}" placeholder="${batchDate || todayStr()}" />
        ${noAiDate ? '<small class="need-date-hint">AI 未识别归属日, 请确认</small>' : ''}
      </td>
      <td><input data-field="deadline_time" type="datetime-local" class="js-datetimepicker" value="${it.deadline_time ? it.deadline_time.slice(0,16) : ''}" /></td>
      <td><input data-field="is_optional" type="checkbox" ${it.is_optional ? 'checked' : ''} /></td>
      <td><input data-field="est_minutes" type="number" min="1" max="180" value="${it.est_minutes || 15}" /></td>
      <td class="col-conf">${confidenceDot(it.confidence)}</td>
      <td class="col-src"><small title="${escapeAttr(it.source_span || '')}">${escapeAttr(it.source_span || '')}</small></td>
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

function collectRows(result) {
  const rows = [...result.querySelectorAll('tbody tr')];
  return rows.map(tr => {
    const get = f => tr.querySelector(`[data-field="${f}"]`);
    const due = get('due_date').value;
    const dlRaw = get('deadline_time').value;
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
      deadline_time: dlRaw ? dlRaw + ':00' : null,
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
      toast('预估分钟必须在 1~180'); return;
    }
  }
  try {
    const r = await api('POST', '/assignments/batch', { batch_id: batchId, items });
    const skipped = r.skipped || [];
    if (skipped.length) {
      // 去重是静默的, 但必须告诉家长跳过了什么 —— 否则"少了几条"会让人以为丢数据
      const names = skipped.slice(0, 3).map(s => `${s.subject}·${s.task_name}`).join('、');
      const more = skipped.length > 3 ? ` 等 ${skipped.length} 条` : '';
      toast(`已入库 ${r.created_ids.length} 条, 跳过重复 ${skipped.length} 条: ${names}${more}`, 6000);
    } else {
      toast(`已入库 ${r.created_ids.length} 条`);
    }
    result.innerHTML = '';
    rawTextarea.value = '';
    document.getElementById('char-count-num').textContent = '0';
    const cc = document.getElementById('char-count');
    if (cc) cc.classList.remove('warn');
    switchTab('board');
  } catch (e) { toast('入库失败: ' + e.message); }
}

// 手动添加 modal
function openManualAddModal() {
  const m = document.getElementById('modal');
  m.classList.remove('hidden');
  document.getElementById('modal-title').textContent = '手动添加作业';
  document.getElementById('modal-content').innerHTML = `
    <div class="row">
      <label>科目
        <select id="m-subject"><option>语文</option><option>数学</option><option>英语</option></select>
      </label>
      <label>预估分
        <input id="m-est" type="number" value="15" min="1" max="180" style="width:80px;" inputmode="numeric" />
      </label>
    </div>
    <div class="row" style="flex-direction:column; align-items:stretch;">
      <label>任务名 <input id="m-name" placeholder="如: 完成知能 P14" /></label>
      <label>归属日 <input id="m-due" type="date" class="js-datepicker" value="${todayStr()}" /></label>
      <label>截止时间 (可选) <input id="m-dl" type="datetime-local" class="js-datetimepicker" /></label>
      <label><input id="m-opt" type="checkbox" /> 选做</label>
    </div>
  `;
  // modal-content 每次打开都是新 DOM, 所以在这里绑定而不是页面启动时
  initDatePickersIn(document.getElementById('modal-content'));
  // 显式恢复成"取消 + 确定": 必须调 setModalActions, 不能只赋 onclick ——
  // 家长若先点过"原文"(只读弹窗, 那里隐藏了取消并把确定改成"返回"),
  // 这里不重置就会看到一个没有取消按钮、写着"返回"的提交弹窗。
  setModalActions({
    mode: 'confirm',
    onOk: async () => {
      const dl = document.getElementById('m-dl').value;
      const items = [{
        subject: document.getElementById('m-subject').value,
        task_name: document.getElementById('m-name').value.trim(),
        due_date: document.getElementById('m-due').value,
        // 修正: 原写法 (v || '') + ':00' || null 因 + 优先级高于 ||,
        // 空值会得到字符串 ":00" (truthy), 右侧 null 永远不触发,
        // 后端 pydantic 解析 ":00" 失败 -> 4003, 手动添加不填截止时间必失败。
        deadline_time: dl ? dl + ':00' : null,
        is_optional: document.getElementById('m-opt').checked,
        est_minutes: parseInt(document.getElementById('m-est').value, 10),
        source_span: '(手动添加)',
        confidence: 'high',
      }];
      if (!items[0].task_name) { toast('任务名必填'); return; }
      if (!items[0].due_date) { toast('归属日必填'); return; }
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
  grid.innerHTML = '';
  // 周一起始: 后端 first_weekday 是 Monday=0, 与 cal-weekdays 表头顺序一致。
  for (let i = 0; i < r.first_weekday; i++) {
    grid.appendChild(el('<div class="cal-cell blank"></div>'));
  }
  for (let d = 1; d <= r.days_in_month; d++) {
    const ds = dateStr(r.year, r.month, d);
    const day = r.days[ds];
    const weekday = (r.first_weekday + d - 1) % 7;
    const cell = el(`
      <button class="cal-cell ${dayState(day)} ${ds === _calSelected ? 'selected' : ''} ${weekday >= 5 ? 'we' : ''}"
              data-date="${ds}" aria-label="${ds}">
        <span class="cal-day">${d}</span>
        ${day && day.total ? `<span class="cal-badge">${day.required ? `${day.required_done}/${day.required}` : '选'}</span>` : ''}
      </button>
    `);
    cell.addEventListener('click', () => selectDate(ds));
    grid.appendChild(cell);
  }
}

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
    list.forEach(item => c.querySelector('.card-body').appendChild(renderAssignment(item, true, refreshCalendarDetail)));
    root.appendChild(c);
  };
  section('待完成', pending);
  section('已收尾', settled);

  if (optional.length) {
    const optDone = optional.filter(i => i.status === 'done').length;
    const c = el(`<details class="card optional-card"><summary class="card-title">选做 (${optional.length})<span class="count">${optDone}/${optional.length}</span></summary><div class="card-body"></div></details>`);
    optional.forEach(item => c.querySelector('.card-body').appendChild(renderAssignment(item, true, refreshCalendarDetail)));
    root.appendChild(c);
  }
}

// 历史日期改状态后要同时刷新日历格子, 但不整页重载,
// 否则家长点一下"完成"就被弹回日历页顶部。
function refreshCalendarDetail() {
  if (_calData) {
    api('GET', `/assignments/calendar?year=${_calYear}&month=${_calMonth}`)
      .then(r => { _calData = r; renderCalendar(); })
      .catch(() => {});
  }
  loadList(_calSelected);
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
document.querySelectorAll('.week-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.week-btn').forEach(x => {
      x.classList.remove('primary'); x.classList.add('ghost');
    });
    b.classList.remove('ghost'); b.classList.add('primary');
    loadWeekly(b.dataset.week);
  });
});

async function loadWeekly(week) {
  const root = document.getElementById('weekly-content');
  root.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const r = await api('GET', `/analytics/weekly?week=${week}`);
    root.innerHTML = `
      <div class="card">
        <strong>${r.week_start} ~ ${r.week_end}</strong>
        <span class="muted"> · 样本 ${r.sample_size} 条</span>
      </div>
    `;
    if (!r.by_type.length) {
      root.appendChild(el(`<div class="muted">${r.note || '本周无完成数据'}</div>`));
      return;
    }
    const tbl = el(`<table>
      <tr><th>科目</th><th>类型</th><th>样本</th><th>估</th><th>实</th><th>偏差</th><th>建议</th></tr>
    </table>`);
    r.by_type.forEach(b => {
      const diffCls = b.diff_minutes > 0 ? 'diff-pos' : 'diff-neg';
      tbl.appendChild(el(`<tr>
        <td><span class="subject-tag ${b.subject}">${b.subject}</span></td>
        <td>${b.task_type}</td>
        <td>${b.sample_size}</td>
        <td>${b.avg_est_minutes}</td>
        <td>${b.avg_actual_minutes}</td>
        <td class="${diffCls}">${b.diff_minutes > 0 ? '+' : ''}${b.diff_minutes}分 (${b.diff_pct}%)</td>
        <td><small>${b.advice}</small></td>
      </tr>`));
    });
    root.appendChild(tbl);
    if (r.top_overestimate.length) {
      root.appendChild(el(`<h3>高估 TOP (你估多了)</h3>`));
      r.top_overestimate.forEach(b => root.appendChild(el(`<div class="advice-card">${b.advice}</div>`)));
    }
    if (r.top_underestimate.length) {
      root.appendChild(el(`<h3>低估 TOP (你估少了)</h3>`));
      r.top_underestimate.forEach(b => root.appendChild(el(`<div class="advice-card">${b.advice}</div>`)));
    }
  } catch (e) {
    if (e.message.includes('4011')) {
      root.innerHTML = `<div class="card muted">样本不足 (需 ≥ 5 条已完成且有实际耗时), 暂不生成偏差表</div>`;
    } else {
      root.innerHTML = `<div class="error-banner">复盘失败: ${e.message}</div>`;
    }
  }
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
            toast('已保存');
          } catch (e) { alert('保存失败: ' + e.message); }
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
          } catch (e) { alert('保存失败: ' + e.message); }
        });
      }

      wrap.appendChild(card);
    });
    root.innerHTML = '';
    root.appendChild(wrap);
  } catch (e) {
    root.innerHTML = `<div class="error-banner">加载失败: ${e.message}</div>`;
  }
}

document.getElementById('btn-export').addEventListener('click', () => {
  window.location.href = API + '/export';
});

// ====== 启动 ======
loadHealth();
loadBoard();
setInterval(loadHealth, 60000);