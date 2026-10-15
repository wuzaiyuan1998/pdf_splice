/* ============================================================
   PDF 单面拼接器 —— 前端逻辑
   三列：PDF_A | PDF_B | PDF_out
   拖拽用 HTML5 原生 DnD，服务端只存状态，前端按快照整体重绘
   ============================================================ */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const app = {
  data: null,      // 服务端状态快照
  drag: null,      // 当前拖拽的东西 {type:'src'|'out', ...}
  zoom: null,      // 放大预览 {slot,index}
  pickSlot: 'A',   // 文件选择框当前归属
  view: 'grid',    // 布局样式：grid（网格） | preview（竖向预览）
};

/* ------------------------------------------------ 请求层 */

async function request(url, options) {
  const res = await fetch(url, options);
  const type = res.headers.get('content-type') || '';
  if (!type.includes('json')) {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return null;
  }
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (data && data.ok === false) throw new Error(data.error || '操作失败');
  if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
  return data;
}

const post = (url, body) => request(url, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body || {}),
});

/* ------------------------------------------------ 小工具 */

const thumbUrl = (slot, index, rot, size = 240) =>
  `/api/thumb/${slot}/${index}?r=${((rot % 360) + 360) % 360}&size=${size}`;

/* 网格样式取小图（一屏多页），竖向预览样式取大图（按栏宽铺满整页） */
const thumbSizeFor = (kind) => (app.view === 'preview' ? 760 : kind === 'row' ? 120 : 240);

function fmtSize(bytes) {
  if (!bytes) return '0 B';
  const mb = bytes / 1048576;
  return mb >= 1 ? mb.toFixed(2) + ' MB' : (bytes / 1024).toFixed(0) + ' KB';
}

function toast(message, kind = '', ms = 3200) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, ms - 260);
  setTimeout(() => el.remove(), ms);
}

const span = (cls, text) => {
  const el = document.createElement('span');
  el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};

const toolBtn = (act, text, title) => {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.dataset.act = act;
  btn.title = title || '';
  btn.textContent = text;
  return btn;
};

async function guard(promise, okMessage) {
  try {
    const data = await promise;
    if (data) apply(data);
    if (okMessage) toast(okMessage, 'ok');
    return true;
  } catch (err) {
    toast(err.message || String(err), 'err', 5000);
    return false;
  }
}

/* ------------------------------------------------ 状态同步 */

function apply(snapshot) {
  app.data = snapshot;
  if (!$('#lightbox').classList.contains('hidden')) renderLightbox();
  renderAll();
}

async function refresh() {
  try {
    app.data = await request('/api/state');
    renderAll();
  } catch (err) {
    toast('连接后端失败：' + err.message, 'err', 6000);
  }
}

function renderAll() {
  if (!app.data) return;
  renderDoc('A');
  renderDoc('B');
  renderOut();
  syncControls();
}

function renderStats() {
  const a = app.data.docs.A, b = app.data.docs.B, out = app.data.out;
  const modeText = { interleave: '自动交错', concat: '顺序拼接', manual: '手动排布' }[app.data.mode] || '';
  $('#stats').textContent =
    `A：${a.loaded ? a.page_count + ' 页' : '—'}　B：${b.loaded ? b.page_count + ' 页' : '—'}　` +
    `成品：${out.length} 页　·　${modeText}`;
}

/* ------------------------------------------------ 第一 / 第二列 */

function renderDoc(slot) {
  const doc = app.data.docs[slot];
  const col = $(`.col[data-slot="${slot}"]`);
  const box = $('#pages' + slot);

  col.querySelector('.fname').textContent = doc.loaded
    ? `${doc.name} · ${doc.page_count} 页 · ${fmtSize(doc.size)}`
    : '未载入';
  col.querySelector('.count').textContent = doc.loaded ? doc.page_count + ' 页' : '';

  box.textContent = '';
  if (!doc.loaded) {
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.innerHTML = '点击这里载入 <b>PDF</b><br>或把文件拖进本栏<br>可一次选多个文件（按顺序合并成一叠）';
    hint.addEventListener('click', () => pickFile(slot));
    box.appendChild(hint);
    return;
  }
  doc.pages.forEach((p) => box.appendChild(thumbCard(slot, p.index, doc.rots[p.index])));
}

function thumbCard(slot, index, rot) {
  const card = document.createElement('div');
  card.className = 'page';
  card.draggable = true;
  card.dataset.slot = slot;
  card.dataset.index = String(index);
  card.title = `PDF_${slot} 第 ${index + 1} 页 · 点击放大预览`;

  const thumb = document.createElement('div');
  thumb.className = 'ph-thumb';
  const img = document.createElement('img');
  img.src = thumbUrl(slot, index, rot, thumbSizeFor('card'));
  img.alt = slot + (index + 1);
  img.draggable = false;
  img.loading = 'lazy';
  thumb.appendChild(img);

  const meta = document.createElement('div');
  meta.className = 'ph-meta';
  meta.append(span('tag', slot + (index + 1)), span('rot', rot ? rot + '°' : ''));

  const tools = document.createElement('div');
  tools.className = 'ph-tools';
  tools.append(
    toolBtn('rot-l', '↺', '逆时针 90°'),
    toolBtn('rot-r', '↻', '顺时针 90°'),
    toolBtn('add', '＋', '插入到成品末尾'),
  );

  card.append(thumb, meta, tools);
  return card;
}

/* ------------------------------------------------ 第三列（成品） */

function renderOut() {
  const box = $('#pagesOut');
  const out = app.data.out;

  $('#outCount').textContent = out.length ? out.length + ' 页' : '';
  $('#outInfo').textContent = out.length
    ? `成品 ${out.length} 页 · ${({ interleave: '自动交错', concat: '顺序拼接', manual: '手动排布' })[app.data.mode] || ''}`
    : '成品（空）';

  box.textContent = '';
  if (!out.length) {
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.innerHTML = '成品还是空的<br>点上方 <b>生成成品</b> 自动交错<br>或把 A / B 的页面拖进来手动排';
    box.appendChild(hint);
    return;
  }
  out.forEach((item, pos) => box.appendChild(outRow(item, pos)));
}

function outRow(item, pos) {
  const row = document.createElement('div');
  row.className = 'out-row';
  row.draggable = true;
  row.dataset.slot = 'OUT';
  row.dataset.pos = String(pos);
  row.dataset.srcSlot = item.slot;
  row.dataset.srcIndex = String(item.index);
  row.title = '来自 PDF_' + item.slot + ' 第 ' + (item.index + 1) + ' 页 · 拖动可重排 · 点击可放大';

  const thumb = document.createElement('div');
  thumb.className = 'thumb';
  const img = document.createElement('img');
  img.src = thumbUrl(item.slot, item.index, item.rot, thumbSizeFor('row'));
  img.alt = '';
  img.draggable = false;
  img.loading = 'lazy';
  thumb.appendChild(img);

  const from = document.createElement('div');
  from.className = 'from';
  from.innerHTML = `<b>${item.slot}${item.index + 1}</b> · 来自 PDF_${item.slot} 第 ${item.index + 1} 页`;
  if (item.rot % 360) from.appendChild(span('rot-tag', '· 转 ' + item.rot + '°'));

  const tools = document.createElement('div');
  tools.className = 'row-tools';
  tools.append(
    toolBtn('rot-l', '↺', '逆时针 90°'),
    toolBtn('rot-r', '↻', '顺时针 90°'),
    toolBtn('del', '×', '从成品中移除'),
  );

  row.append(span('idx', String(pos + 1)), thumb, from, tools);
  return row;
}

/* ------------------------------------------------ 控件状态 */

function syncControls() {
  const d = app.data;
  $$('#segMode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === d.mode));
  $$('#segFirst button').forEach((b) => b.classList.toggle('on', b.dataset.first === d.first));
  $('#chkReverse').checked = !!d.reverse_second;
  renderStats();
}

/* 布局样式：网格 / 竖向预览。换样式要重新取图（两种样式的缩略图尺寸不同） */
function setView(view) {
  app.view = view === 'preview' ? 'preview' : 'grid';
  document.documentElement.dataset.view = app.view;
  localStorage.setItem('pdf_splice_view', app.view);
  $$('#segView button').forEach((b) => b.classList.toggle('on', b.dataset.view === app.view));
  renderAll();
}

/* 只改参数：本地先更新，再让服务端重建（有载入 PDF 时才真的重建） */
function setBuild(patch) {
  const payload = {
    mode: patch.mode ?? app.data.mode,
    first: patch.first ?? app.data.first,
    reverse_second: patch.reverse_second ?? app.data.reverse_second,
  };
  if (payload.mode === 'manual') payload.mode = 'interleave';
  return guard(post('/api/build', payload));
}

/* ------------------------------------------------ 文件载入 */

function pickFile(slot) {
  app.pickSlot = slot;
  const input = $('#fileInput');
  input.value = '';           // 允许重复选同一个文件
  input.click();
}

async function uploadFiles(slot, files) {
  const pdfs = files.filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
  if (!pdfs.length) { toast('只支持 PDF 文件', 'err'); return; }
  if (pdfs.length < files.length) toast('已忽略 ' + (files.length - pdfs.length) + ' 个非 PDF 文件', 'warn');

  const fd = new FormData();
  fd.append('slot', slot);
  pdfs.forEach((f) => fd.append('file', f));

  const tip = pdfs.length > 1 ? `正在合并载入 ${pdfs.length} 个文件到 PDF_${slot}…` : `正在载入 PDF_${slot}…`;
  toast(tip);
  const data = await request('/api/upload', { method: 'POST', body: fd });
  apply(data);
  toast(`PDF_${slot} 载入完成：${data.docs[slot].page_count} 页`, 'ok');
}

/* ------------------------------------------------ 事件：点击 */

document.addEventListener('click', async (e) => {
  // 1) 栏头工具按钮
  const actBtn = e.target.closest('button[data-act]');
  if (actBtn) {
    const act = actBtn.dataset.act;
    const slot = actBtn.dataset.slot || actBtn.closest('.page')?.dataset.slot;

    if (act === 'pick') return pickFile(slot);

    if (act === 'rot-all') {
      const delta = Number(actBtn.dataset.delta || 90);
      return guard(post('/api/rotate', { slot, delta, index: null }));
    }
    if (act === 'add-all') return guard(post('/api/out/insert', { slot, all: true }));

    if (act === 'clear') {
      if (!confirm(`确定清空 PDF_${slot} 吗？（成品里来自这一叠的页面也会移除）`)) return;
      return guard(post('/api/clear_slot', { slot }));
    }

    // 2) 缩略图上的按钮
    const card = actBtn.closest('.page');
    if (card) {
      const index = Number(card.dataset.index);
      if (act === 'rot-l') return guard(post('/api/rotate', { slot, index, delta: -90 }));
      if (act === 'rot-r') return guard(post('/api/rotate', { slot, index, delta: 90 }));
      if (act === 'add') return guard(post('/api/out/insert', { slot, index }));
    }

    // 3) 成品行上的按钮
    const row = actBtn.closest('.out-row');
    if (row) {
      const pos = Number(row.dataset.pos);
      if (act === 'rot-l') return guard(post('/api/out/rotate', { positions: [pos], delta: -90 }));
      if (act === 'rot-r') return guard(post('/api/out/rotate', { positions: [pos], delta: 90 }));
      if (act === 'del') return guard(post('/api/out/remove', { positions: [pos] }));
    }
    return;
  }

  // 4) 点击缩略图 → 放大预览
  const card = e.target.closest('.page');
  if (card) return openLightbox(card.dataset.slot, Number(card.dataset.index));

  const row = e.target.closest('.out-row');
  if (row) return openLightbox(row.dataset.srcSlot, Number(row.dataset.srcIndex));
});

/* ------------------------------------------------ 事件：栏头 / 参数控件 */

$('#segMode').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-mode]');
  if (btn) setBuild({ mode: btn.dataset.mode });
});
$('#segFirst').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-first]');
  if (btn) setBuild({ first: btn.dataset.first });
});
$('#chkReverse').addEventListener('change', (e) => setBuild({ reverse_second: e.target.checked }));

$('#segView').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-view]');
  if (btn) setView(btn.dataset.view);
});

$('#btnBuild').addEventListener('click', () => {
  if (!app.data.docs.A.loaded && !app.data.docs.B.loaded) return toast('先在 A / B 里载入 PDF', 'warn');
  const mode = app.data.mode === 'manual' ? 'interleave' : app.data.mode;
  guard(post('/api/build', { mode, first: app.data.first, reverse_second: app.data.reverse_second }),
    '成品已重建');
});

$('#btnAutosort').addEventListener('click', async () => {
  try {
    const snap = await request('/api/autosort', { method: 'POST' });
    apply(snap);
    const info = snap.autosort || {};
    toast(info.note || '已完成', info.ok ? 'ok' : 'warn', 5200);
  } catch (err) { toast(err.message, 'err', 5000); }
});

$('#btnClearOut').addEventListener('click', () => {
  if (!app.data.out.length) return;
  if (!confirm('确定清空成品吗？')) return;
  guard(post('/api/out/clear'));
});

$('#btnSwap').addEventListener('click', () => guard(post('/api/swap'), '已交换 A / B'));

$('#btnOpenDir').addEventListener('click', async () => {
  try {
    const r = await post('/api/open_output');
    toast('已打开输出目录：' + r.path, 'ok');
  } catch (err) { toast(err.message, 'err'); }
});

$('#btnExport').addEventListener('click', async () => {
  if (!app.data.out.length) return toast('成品还是空的，先点「生成成品」', 'warn');
  const btn = $('#btnExport');
  btn.disabled = true;
  try {
    const res = await post('/api/export', { name: $('#outName').value.trim() });
    toast(`已生成 ${res.name}（${res.pages} 页 / ${fmtSize(res.size)}）\n保存于：${res.path}`, 'ok', 7000);
    const a = document.createElement('a');
    a.href = res.url;
    a.download = res.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } catch (err) {
    toast(err.message, 'err', 6000);
  } finally {
    btn.disabled = false;
  }
});

/* 文件选择框（A / B 共用） */
$('#fileInput').addEventListener('change', async (e) => {
  const files = Array.from(e.target.files || []);
  if (!files.length) return;
  try { await uploadFiles(app.pickSlot, files); } catch (err) { toast(err.message, 'err', 6000); }
});

/* ------------------------------------------------ 事件：拖拽 */

document.addEventListener('dragstart', (e) => {
  const row = e.target.closest ? e.target.closest('.out-row') : null;
  const card = e.target.closest ? e.target.closest('.page') : null;
  let info = null;
  if (row) info = { type: 'out', pos: Number(row.dataset.pos) };
  else if (card) info = { type: 'src', slot: card.dataset.slot, index: Number(card.dataset.index) };
  if (!info) return;

  app.drag = info;
  e.dataTransfer.effectAllowed = 'move';
  try { e.dataTransfer.setData('text/plain', JSON.stringify(info)); } catch (_) { /* 忽略 */ }
  (row || card).classList.add('dragging');
  document.body.classList.add('dragging-active');
});

document.addEventListener('dragend', () => {
  app.drag = null;
  $$('.dragging').forEach((el) => el.classList.remove('dragging'));
  document.body.classList.remove('dragging-active');
  $$('.col.drag-over').forEach((el) => el.classList.remove('drag-over'));
  removePlaceholder();
});

function dropIndex(clientY) {
  const rows = $$('.out-row', $('#pagesOut'));
  for (let i = 0; i < rows.length; i += 1) {
    const r = rows[i].getBoundingClientRect();
    if (clientY < r.top + r.height / 2) return i;
  }
  return rows.length;
}

function showPlaceholder(index) {
  const box = $('#pagesOut');
  let ph = box.querySelector('.ph');
  if (!ph) {
    ph = document.createElement('div');
    ph.className = 'ph';
  }
  const hint = box.querySelector('.empty-hint');
  if (hint) hint.remove();
  const rows = $$('.out-row', box);
  box.insertBefore(ph, rows[index] || null);
}

function removePlaceholder() {
  const box = $('#pagesOut');
  const ph = box.querySelector('.ph');
  if (ph) ph.remove();
  if (app.data && !app.data.out.length && !box.querySelector('.empty-hint')) renderOut();
}

$$('.col').forEach((col) => {
  const slot = col.dataset.slot;

  col.addEventListener('dragover', (e) => {
    const dt = e.dataTransfer || {};
    const files = Array.from(dt.types || []).includes('Files');
    if (files) {
      if (slot === 'OUT') return;                 // 外部文件只能拖到 A / B
      e.preventDefault();
      dt.dropEffect = 'copy';
      col.classList.add('drag-over');
      return;
    }
    if (!app.drag) return;
    e.preventDefault();
    dt.dropEffect = 'move';
    col.classList.add('drag-over');
    if (slot === 'OUT') showPlaceholder(dropIndex(e.clientY));
  });

  col.addEventListener('dragleave', (e) => {
    if (e.relatedTarget && col.contains(e.relatedTarget)) return;
    col.classList.remove('drag-over');
    if (slot === 'OUT') removePlaceholder();
  });

  col.addEventListener('drop', async (e) => {
    const info = app.drag;
    const files = e.dataTransfer && e.dataTransfer.files;

    // ① 从资源管理器拖文件进来
    if (files && files.length && slot !== 'OUT') {
      e.preventDefault();
      col.classList.remove('drag-over');
      try { await uploadFiles(slot, Array.from(files)); } catch (err) { toast(err.message, 'err', 6000); }
      return;
    }

    if (!info) return;
    e.preventDefault();
    col.classList.remove('drag-over');

    if (slot === 'OUT') {
      removePlaceholder();
      const at = dropIndex(e.clientY);
      if (info.type === 'src') {
        await guard(post('/api/out/insert', { slot: info.slot, index: info.index, at }));
      } else {
        await guard(post('/api/out/move', { from: info.pos, to: at }));
      }
    } else if (info.type === 'out') {
      // 把成品页拖回源栏 = 从成品里删掉
      const ok = await guard(post('/api/out/remove', { positions: [info.pos] }));
      if (ok) toast('已从成品移除该页', 'ok');
    }
    removePlaceholder();
  });
});

/* ------------------------------------------------ 放大预览 */

function openLightbox(slot, index) {
  const doc = app.data.docs[slot];
  if (!doc || !doc.loaded) return;
  app.zoom = { slot, index };
  renderLightbox();
  $('#lightbox').classList.remove('hidden');
}

function closeLightbox() {
  app.zoom = null;
  $('#lightbox').classList.add('hidden');
  $('#lbImg').removeAttribute('src');
}

function renderLightbox() {
  if (!app.zoom || !app.data) return;
  const { slot, index } = app.zoom;
  const doc = app.data.docs[slot];
  if (!doc || !doc.loaded || !(index >= 0 && index < doc.page_count)) return closeLightbox();
  const rot = doc.rots[index];
  $('#lbTitle').textContent =
    `PDF_${slot} 第 ${index + 1} / ${doc.page_count} 页` + (rot ? ` · 已旋转 ${rot}°` : '');
  const img = $('#lbImg');
  img.src = thumbUrl(slot, index, rot, 1400);
  img.alt = `${slot}${index + 1}`;
}

function zoomStep(delta) {
  if (!app.zoom) return;
  const doc = app.data.docs[app.zoom.slot];
  if (!doc || !doc.loaded) return;
  app.zoom.index = (app.zoom.index + delta + doc.page_count) % doc.page_count;
  renderLightbox();
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('#lightbox [data-lb]');
  if (!btn) {
    if (e.target.id === 'lightbox' || e.target.classList.contains('lb-stage')) closeLightbox();
    return;
  }
  const act = btn.dataset.lb;
  if (act === 'close') return closeLightbox();
  if (act === 'prev') return zoomStep(-1);
  if (act === 'next') return zoomStep(1);
  if (!app.zoom) return;
  const delta = act === 'rot-l' ? -90 : 90;
  guard(post('/api/rotate', { slot: app.zoom.slot, index: app.zoom.index, delta }), null)
    .then(() => renderLightbox());
});

document.addEventListener('keydown', (e) => {
  if (!app.zoom) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft' || e.key === 'PageUp') zoomStep(-1);
  else if (e.key === 'ArrowRight' || e.key === 'PageDown') zoomStep(1);
  else if (e.key === 'r' || e.key === 'R') {
    guard(post('/api/rotate', { slot: app.zoom.slot, index: app.zoom.index, delta: 90 }))
      .then(() => renderLightbox());
  }
});

/* ------------------------------------------------ 主题 */

function initTheme() {
  const saved = localStorage.getItem('pdf_splice_theme');
  const auto = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.dataset.theme = saved || auto;
}
$('#btnTheme').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('pdf_splice_theme', next);
});

/* ------------------------------------------------ 启动 */

initTheme();
setView(localStorage.getItem('pdf_splice_view') || 'grid');
refresh();
