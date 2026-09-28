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

function fmtMin(v) {
  if (v == null || v === '') return '—';
  return v + '分';
}

function fmtDate(s) {
  if (!s) return '—';
  return s.slice(0, 10);
}

function fmtDateTime(s) {
  if (!s) return '—';
  return s.replace('T', ' ').slice(0, 16);
}

// 置信度 Pill (设计文档 §5.4 + §9 坑 3)
function confidencePill(level) {
  const label = { high: '高', medium: '? 推断', low: '⚠ 需核对' }[level] || level;
  return `<span class="confidence-pill ${level}" title="${
    level === 'low' ? 'AI 推断不完整, 建议点开原文核对'
    : level === 'medium' ? 'AI 推断, 建议核对原文'
    : 'AI 高度确信'
  }">${label}</span>`;
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
  if (name === 'board') loadBoard();
  if (name === 'list') loadList();
  if (name === 'weekly') loadWeekly('current');
  if (name === 'settings') loadSettings();
}
document.querySelectorAll('#tabs button').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});
window.switchTab = switchTab;

// ====== Health ======
async function loadHealth() {
  const bar = document.getElementById('health-bar');
  try {
    const h = await api('GET', '/health');
    bar.textContent = `DB ok · 模型 ${h.ai_model || '-'} · AI ${h.ai_reachable ? '✓ 可用' : '✗ 未配置'}`;
    bar.className = h.ai_reachable ? 'ok-true' : 'ok-false';
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
    bar.textContent = 'health 失败: ' + e.message;
    bar.className = 'ok-false';
  }
}

// ====== 看板 ======
// 全量重建 DOM 会把滚动位置弹回顶部, 家长点一下状态就得重新往下滚。
// 重建前后记住 scrollTop 并还原。
let _boardScroll = 0;
async function loadBoard() {
  const root = document.getElementById('board-content');
  const scroller = root.closest('.view.active') || document.scrollingElement;
  _boardScroll = scroller ? scroller.scrollTop : 0;
  root.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const b = await api('GET', '/board/today');
    root.innerHTML = '';
    if (b.is_late_night) {
      root.appendChild(el('<p class="muted">已是凌晨, 注意休息</p>'));
    }
    if (!b.has_data) {
      // 空状态 (设计文档 §7.6)
      root.appendChild(el(`
        <div class="empty">
          <div class="empty-icon">📋</div>
          <p>今天还没有作业</p>
          <button class="primary" data-action="goto-entry">去录入</button>
          <p class="muted">或者手动添加一条作业</p>
        </div>
      `));
      root.querySelector('[data-action="goto-entry"]').addEventListener('click', () => switchTab('entry'));
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
          <span>已收尾 <strong>${p.settled}</strong> / 必做 <strong>${p.required}</strong> · ${pct}%</span>
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
function renderAssignment(item, withStatus) {
  // pending_actual: done 但未填实耗时 (设计文档 §9 坑 2)
  const isPendingActual = item.status === 'done' && item.actual_minutes == null;
  const confClass = `confidence-${item.confidence}`; // 给整行加边条 / 底色
  const row = el(`
    <div class="assignment-row ${item.status} ${confClass} ${isPendingActual ? 'pending-actual' : ''}" data-id="${item.id}">
      <div class="row-main">
        <span class="subject-tag ${item.subject}">${item.subject}</span>
        <div style="flex:1;">
          <div class="task-name">${escapeAttr(item.task_name)}${item.is_optional ? '<span class="optional-tag">选做</span>' : ''}</div>
        </div>
        ${item.est_minutes != null ? `<div class="est-min">${item.est_minutes}</div>` : '<div class="est-min muted">—</div>'}
      </div>
      <div class="row-meta">
        ${item.deadline_time ? `<span>⏰ ${fmtDateTime(item.deadline_time)}</span>` : ''}
        ${item.task_type ? `<span>类型: ${item.task_type}</span>` : ''}
        ${confidencePill(item.confidence)}
        ${item.source_span ? `<span class="source-link" data-action="show-source">原文</span>` : ''}
        ${item.actual_minutes != null ? `<span>实际 <strong>${item.actual_minutes}</strong> 分</span>` : ''}
      </div>
      ${withStatus ? '<div class="row-actions"></div>' : ''}
    </div>
  `);

  if (!withStatus) return row;

  const actions = row.querySelector('.row-actions');

  // 三个动作: 开始(推进) / 完成(收尾) / 选做(可来回切换)
  // 不再平铺 todo/doing/done/skipped 四个互斥按钮 —— 那会让一行塞进 4+ 个控件,
  // 且"跳过"与"选做"语义重叠, 家长分不清该点哪个。
  const patchStatus = async (s, btn) => {
    btn.disabled = true;                       // 请求锁: 防快速连点产生并发 PATCH
    try {
      await api('PATCH', `/assignments/${item.id}/status`, { status: s });
      if (s === 'done' && item.actual_minutes == null) {
        toast('已标记完成, 请补填实际耗时 (不填不影响, 但周复盘不计)');
      } else if (s === 'skipped') {
        toast('已跳过, 不计入完成数');
      }
      await loadBoard();
    } catch (e) {
      btn.disabled = false;
      toast('状态更新失败: ' + e.message);      // 用 toast 而非 alert, 不打断流程
    }
  };

  // 开始: todo -> doing
  if (item.status === 'todo') {
    const b = el(`<button class="status-btn doing" aria-label="标记为进行中">开始</button>`);
    b.addEventListener('click', () => patchStatus('doing', b));
    actions.appendChild(b);
  }
  // 完成: todo/doing -> done
  if (item.status === 'todo' || item.status === 'doing') {
    const b = el(`<button class="status-btn done" aria-label="标记为已完成">完成</button>`);
    b.addEventListener('click', () => patchStatus('done', b));
    actions.appendChild(b);
  }
  // 跳过: 未收尾的项才需要; 已跳过的项给"撤销"
  if (item.status === 'todo' || item.status === 'doing') {
    const b = el(`<button class="status-btn skipped" aria-label="今天不做这条">今天不做</button>`);
    b.addEventListener('click', () => patchStatus('skipped', b));
    actions.appendChild(b);
  } else if (item.status === 'skipped') {
    const b = el(`<button class="status-btn todo" aria-label="撤销跳过">撤销跳过</button>`);
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
        await loadBoard();
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
        await loadBoard();
      } catch (e) { b.disabled = false; toast('操作失败: ' + e.message); }
    });
    actions.appendChild(b);
  }

  // pending_actual 实耗时输入 (设计文档 §9 坑 2 — 高亮 + 自动 focus)
  if (isPendingActual) {
    const inputWrap = el('<div class="row" style="margin:0;"></div>');
    const input = el(`<input type="number" class="actual-input" placeholder="填实际分钟" min="0" max="240" style="width:90px;" inputmode="numeric" />`);
    const saveBtn = el('<button class="primary">保存实耗</button>');
    const skipBtn = el('<button class="ghost">跳过</button>');
    saveBtn.addEventListener('click', async () => {
      const v = parseInt(input.value, 10);
      if (isNaN(v) || v < 0 || v > 240) {
        input.classList.add('error');
        toast('实际分钟 0~240');
        return;
      }
      try {
        await api('PATCH', `/assignments/${item.id}/completion`, { actual_minutes: v });
        toast('已记录');
        loadBoard();
      } catch (e) { toast('保存失败: ' + e.message); }
    });
    skipBtn.addEventListener('click', () => {
      input.classList.remove('error');
      input.value = '';
      input.placeholder = '不填则不计入周复盘';
      toast('本次不记录实耗, 之后仍可补填');
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveBtn.click(); });
    inputWrap.append(input, saveBtn, skipBtn);
    actions.appendChild(inputWrap);
    // 进入视口自动 focus (设计文档 §9 坑 2)
    setTimeout(() => input.focus(), 50);
  } else if (item.actual_minutes != null) {
    // 已填, 给改实耗
    const editBtn = el('<button class="ghost">改实耗</button>');
    editBtn.addEventListener('click', async () => {
      const v = prompt('改实际分钟 (0~240)', item.actual_minutes);
      if (v == null) return;
      const n = parseInt(v, 10);
      if (isNaN(n) || n < 0 || n > 240) { toast('0~240'); return; }
      try {
        await api('PATCH', `/assignments/${item.id}/completion`, { actual_minutes: n });
        loadBoard();
      } catch (e) { toast('保存失败: ' + e.message); }
    });
    actions.appendChild(editBtn);
  }

  // 删除 —— 已完成的作业后端强制禁止删除 (4010), 所以这里直接不渲染,
  // 否则就是给用户一个"点了必然报错"的按钮。
  if (item.status !== 'done') {
    const delBtn = el('<button class="danger">删除</button>');
    delBtn.addEventListener('click', async () => {
      if (!confirm(`确定删除 "${item.task_name}"?`)) return;
      try {
        await api('DELETE', `/assignments/${item.id}`);
        loadBoard();
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

function statusLabel(s) {
  return { todo: '待办', doing: '进行', done: '完成', skipped: '跳过' }[s] || s;
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
    ${item.est_minutes != null ? `<p>预估: ${item.est_minutes} 分钟</p>` : ''}
  `;
  document.getElementById('modal-ok').onclick = () => m.classList.add('hidden');
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
        <input data-field="due_date" type="date" class="${noAiDate ? 'need-date' : ''}"
               value="${it.due_date || ''}" placeholder="${batchDate || todayStr()}" />
        ${noAiDate ? '<small class="need-date-hint">AI 未识别归属日, 请确认</small>' : ''}
      </td>
      <td><input data-field="deadline_time" type="datetime-local" value="${it.deadline_time ? it.deadline_time.slice(0,16) : ''}" /></td>
      <td><input data-field="is_optional" type="checkbox" ${it.is_optional ? 'checked' : ''} /></td>
      <td><input data-field="est_minutes" type="number" min="1" max="180" value="${it.est_minutes || 15}" /></td>
      <td>${confidencePill(it.confidence)}</td>
      <td><small>${escapeAttr(it.source_span || '')}</small></td>
      <td><button class="ghost row-del">删</button></td>
    `;
    tr.querySelector('.row-del').addEventListener('click', () => tr.remove());
    tbody.appendChild(tr);
  });
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
    return {
      checked: get('checked').checked,
      subject: get('subject').value,
      task_name: get('task_name').value.trim(),
      due_date: due,
      deadline_time: dlRaw ? dlRaw + ':00' : null,
      is_optional: get('is_optional').checked,
      est_minutes: parseInt(get('est_minutes').value, 10),
      source_span: tr.querySelector('small').textContent || '',
      confidence: tr.querySelector('.confidence-pill').classList.contains('high') ? 'high'
        : tr.querySelector('.confidence-pill').classList.contains('medium') ? 'medium'
        : tr.querySelector('.confidence-pill').classList.contains('low') ? 'low' : 'medium',
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
      const firstEmpty = result.querySelector('[data-field="due_date"].need-date, [data-field="due_date"]:not([value])');
      if (firstEmpty) firstEmpty.focus();
      return;
    }
    if (!it.est_minutes || it.est_minutes < 1 || it.est_minutes > 180) {
      toast('预估分钟必须在 1~180'); return;
    }
  }
  try {
    const r = await api('POST', '/assignments/batch', { batch_id: batchId, items });
    toast(`已入库 ${r.created_ids.length} 条`);
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
      <label>归属日 <input id="m-due" type="date" value="${todayStr()}" /></label>
      <label>截止时间 (可选) <input id="m-dl" type="datetime-local" /></label>
      <label><input id="m-opt" type="checkbox" /> 选做</label>
    </div>
  `;
  document.getElementById('modal-ok').onclick = async () => {
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
  };
}

// ====== 列表 ======
document.getElementById('list-date').value = todayStr();
// 录入页的批次级默认归属日
document.getElementById('entry-date').value = todayStr();
document.getElementById('btn-load-list').addEventListener('click', () => loadList());
document.getElementById('btn-load-today').addEventListener('click', () => {
  document.getElementById('list-date').value = todayStr();
  loadList();
});
document.getElementById('btn-load-yesterday').addEventListener('click', () => {
  const d = new Date(); d.setDate(d.getDate() - 1);
  // 同样不能用 toISOString (UTC), 凌晨会退到前两天
  const p = n => String(n).padStart(2, '0');
  document.getElementById('list-date').value =
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  loadList();
});

async function loadList() {
  const d = document.getElementById('list-date').value;
  if (!d) return;
  const root = document.getElementById('list-content');
  root.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const r = await api('GET', `/assignments?date=${d}`);
    if (!r.items.length) {
      root.innerHTML = `
        <div class="empty">
          <div class="empty-icon">📋</div>
          <p>${d} 没有作业</p>
        </div>`;
      return;
    }
    const table = el('<table><tr><th>科目</th><th>任务</th><th>预估</th><th>实耗</th><th>状态</th><th>置信</th><th>原文</th></tr></table>');
    r.items.forEach(it => {
      const tr = el(`<tr class="confidence-${it.confidence}">
        <td><span class="subject-tag ${it.subject}">${it.subject}</span></td>
        <td>${escapeAttr(it.task_name)}${it.is_optional ? '<span class="optional-tag">选</span>' : ''}</td>
        <td>${fmtMin(it.est_minutes)}</td>
        <td>${fmtMin(it.actual_minutes)}</td>
        <td>${statusLabel(it.status)}</td>
        <td>${confidencePill(it.confidence)}</td>
        <td><small>${escapeAttr(it.source_span || '')}</small></td>
      </tr>`);
      table.appendChild(tr);
    });
    root.innerHTML = '';
    root.appendChild(table);
  } catch (e) {
    root.innerHTML = `<div class="error-banner">加载失败: ${e.message}</div>`;
  }
}

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
async function loadSettings() {
  const root = document.getElementById('settings-content');
  root.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const r = await api('GET', '/settings');
    const wrap = el('<div></div>');
    r.items.forEach(it => {
      const card = el('<div class="card"></div>');
      card.appendChild(el(`<strong>${it.key}</strong>`));

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
        // 非 JSON object (字符串/数字/数组/解析失败) → 文本框兜底
        const ta = el(`<textarea data-key="${it.key}" rows="3">${escapeAttr(it.value)}</textarea>`);
        card.appendChild(ta);
        const saveBtn = el('<button class="primary save-btn" type="button">保存</button>');
        saveBtn.addEventListener('click', async () => {
          try {
            await api('PATCH', `/settings/${it.key}`, { value: ta.value });
            toast('已保存');
          } catch (e) { alert('保存失败: ' + e.message); }
        });
        card.appendChild(saveBtn);
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